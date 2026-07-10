# Claude Manager

Telegram-бот — пульт управления [Claude Code](https://claude.com/claude-code) с телефона.
Пользователь пишет сообщение в Telegram, бот запускает Claude Code на компьютере,
получает ответ и пересылает обратно.

Бот работает **локально** на компьютере пользователя (не на сервере) и общается с
Claude Code CLI через протокол stream-json: отправляет сообщения через stdin, читает
ответы из stdout.

## Возможности

- **Диалог с Claude Code из Telegram** — отправка сообщений и получение ответов
- **Управление сессиями** — создание (`/new`), переключение (`/N`), остановка (`/stop`),
  дневная нумерация сессий (#1, #2, #3...)
- **Наблюдение в реальном времени** — watcher следит за сессиями и присылает новые
  сообщения, включая сессии, запущенные из терминала
- **Глобальный режим `/all`** — мониторинг сообщений из всех проектов сразу с переходом
  по командам вида `/3s12`
- **Переключение между проектами** — команды `/projects` и `/pN` прямо из Telegram
- **Файлы в обе стороны** — отправка фотографий и документов боту (Claude сам читает
  файл); доставка файлов из ответа агента через маркеры `[SEND_FILE:path]` и
  `[SHOW_FILE:path]`
- **Режим тишины** — команды `Silence on` / `Silence off` подавляют промежуточные
  сообщения, доставляются только финальные ответы
- **Безопасный самоперезапуск** — команда `/restart` в Telegram

## Требования

- Linux с systemd (Ubuntu 22+, Debian 12+, Fedora 38+, Arch)
- Python 3.13
- Claude Code CLI (`claude --version` должна работать в терминале)
- Telegram-бот, созданный через @BotFather

## Быстрый старт

```bash
git clone https://github.com/iiivan83/claude-manager.git
cd claude-manager

python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# заполнить TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, PROJECTS_ROOT_DIR

python -m claude_manager
```

Editable install (`pip install -e .`) обязателен: без него не появится entry point
`claude-manager` в `.venv/bin/`, и запуск под systemd упадёт.

Обязательные переменные `.env`:

- **TELEGRAM_BOT_TOKEN** — токен от @BotFather
- **ALLOWED_USER_IDS** — Telegram-ID владельца (белый список; несколько ID через
  запятую, но только для одного человека с разных устройств)
- **PROJECTS_ROOT_DIR** — корневая папка со всеми проектами для команды `/projects`

Полная пошаговая инструкция — от чистой системы до автозапуска под systemd и
E2E-тестов — в [dev/docs/deployment-guide.md](dev/docs/deployment-guide.md).

## Архитектура

Код организован по слоям с чёткими границами ответственности:

- **Транспортный слой** — приём сообщений из Telegram, доставка ответов, скачивание и
  отправка файлов, оркестрация запросов к CLI (`bot.py`, `telegram_*`-модули,
  `claude_interaction.py`)
- **Бизнес-логика** — сессии, дневная нумерация, проекты, режим тишины
  (`session_manager`, `daily_session_registry`, `project_manager`,
  `silence_mode_registry`)
- **Инфраструктура** — запуск процессов CLI, stream-json, retry, `/stop`
  (`process_*`-модули, `claude_runner`)
- **Мониторинг** — `session_watcher` (активный проект) и `all_projects_monitor`
  (режим `/all`)

Весь код асинхронный (asyncio). Верхний слой может вызывать нижний, но не наоборот.
Справочник протокола stream-json — в
[dev/docs/claude-cli-stream-json-protocol.md](dev/docs/claude-cli-stream-json-protocol.md).

## Тестирование

```bash
python -m pytest tests/ -v
```

- **Юнит-тесты** (`tests/test_*.py`) — по одному файлу на каждый модуль `src/`
- **Интеграционные тесты** (`tests/integration/`) — жизненный цикл сессий, конкуренция
- **E2E-тесты** (`tests/e2e/`) — через Telethon, реальные сообщения в Telegram
  (настройка описана в deployment-guide, шаг 8)

## Документация

- [dev/docs/docs-index.md](dev/docs/docs-index.md) — полный индекс документов проекта
- [dev/docs/brd/brd-user-journeys.md](dev/docs/brd/brd-user-journeys.md) — все
  пользовательские сценарии
- [dev/docs/deployment-guide.md](dev/docs/deployment-guide.md) — развёртывание
- [dev/docs/claude-cli-stream-json-protocol.md](dev/docs/claude-cli-stream-json-protocol.md) —
  протокол общения с Claude Code CLI

## Безопасность

- Токены и секреты живут только в `.env` (не коммитится в git)
- Доступ к боту — по белому списку `ALLOWED_USER_IDS`, проверка при каждом запросе
- Бот архитектурно однопользовательский: несколько ID в белом списке — только для
  одного человека с разных устройств
