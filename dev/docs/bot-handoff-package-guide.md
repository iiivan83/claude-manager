# Claude Manager Handoff Package Guide

## Контекст

Документ описывает, как передавать Claude Manager другому пользователю в виде архива без личных данных и runtime-состояния.

Дата: 12-05-2026

## Что входит в handoff-архив

Архив предназначен для разработки, запуска и исправления бота на другом компьютере. В него должны входить:

- **Исходный код** — `src/claude_manager/`, весь Python-код бота.
- **Тесты** — `tests/`, включая unit, integration и E2E-тесты. E2E-тесты требуют отдельной настройки Telegram-аккаунта.
- **Конфигурационный шаблон** — `.env.example`, безопасный пример переменных окружения без секретов.
- **Зависимости и package metadata** — `pyproject.toml` и `requirements.txt`.
- **Скрипты запуска** — `watch_and_restart.sh`, `start-claude-manager.sh`, `restart-claude-manager.sh`.
- **Документация по боту** — `CLAUDE.md`, `AGENTS.md`, `dev/docs/**`, кроме документов с локальной инфраструктурой владельца, не относящейся к боту.

## Что намеренно не входит в архив

Эти файлы содержат личные данные, секреты, состояние конкретной машины или временные артефакты:

- **`.env` и `.env.local`** — Telegram token, Telegram user IDs, Telethon credentials.
- **`.venv/` и `venv/`** — локальное Python-окружение, привязанное к компьютеру пользователя.
- **`.git/`** — история Git и внутренние файлы репозитория.
- **`sessions.json` и `daily_sessions.json`** — состояние рабочих сессий текущего пользователя.
- **`received_files/`** — файлы, которые пользователь отправлял боту в Telegram.
- **`*.session` и `*.session-journal`** — авторизационные файлы Telethon.
- **`bot.pid`, `bot.log`, `*.log`, `.pytest_cache/`, `__pycache__/`, `.DS_Store`** — runtime, кэши и служебный мусор.
- **`.claude/settings*.json`, `.claude/worktrees/`, `.claude/skills-backup-*`** — локальные настройки Claude Code и резервные копии.
- **`.claude/skills/**` и `.agents/skills/**` symlink mirrors** — локальные ссылки на shared-skills вне проекта. У другого пользователя такие ссылки будут битые, поэтому их нужно восстанавливать отдельно только если он хочет использовать проектные pipeline-скиллы.
- **`dev/docs/router-configuration.md`** — локальная конфигурация сети владельца. Это не документация бота и её нельзя передавать в публичном handoff-архиве.

## Первый запуск у нового пользователя

1. Распаковать архив и перейти в папку проекта.
2. Установить Python 3.13.
3. Установить Claude Code CLI и проверить командой `claude --version`.
4. Создать Telegram-бота через `@BotFather` и получить token.
5. Создать `.env` из шаблона:

```bash
cp .env.example .env
```

6. Заполнить в `.env` обязательные значения:

- **`TELEGRAM_BOT_TOKEN`** — token Telegram-бота от `@BotFather`.
- **`ALLOWED_USER_IDS`** — числовой Telegram ID владельца бота.

7. Создать виртуальное окружение и установить проект:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -c "import claude_manager; print('OK')"
```

8. Запустить бот:

```bash
python -m claude_manager
```

9. Проверить в Telegram:

- Отправить `/new`.
- Получить ответ о новой сессии.
- Отправить тестовое сообщение и убедиться, что ответ пришёл из Claude Code.

## Где читать, если нужно чинить бота

- **`CLAUDE.md`** — главный файл с архитектурой, правилами разработки, командами и важными инвариантами проекта.
- **`dev/docs/docs-index.md`** — навигация по документации.
- **`dev/docs/deployment-guide.md`** — развёртывание от нуля.
- **`dev/docs/bot-launch-infrastructure.md`** — автозапуск на macOS, launchd, venv вне Desktop, диагностика TCC/provenance проблем.
- **`dev/docs/adr/project_architecture.md`** — архитектурное решение по слоям и зависимостям.
- **`dev/docs/brd/brd-user-journeys.md`** — пользовательские сценарии, которые бот должен поддерживать.
- **`dev/docs/claude-cli-stream-json-protocol.md`** — контракт общения с Claude Code CLI через stream-json.
- **`dev/docs/specs/realised/`** — реализованные спецификации модулей.
- **`dev/docs/review-checklists.md`** — чеклисты ревью перед изменениями.

## Главные точки входа в код

- **`src/claude_manager/main.py`** — запуск приложения, логирование, файловая блокировка, polling Telegram.
- **`src/claude_manager/bot.py`** — обработчики Telegram-команд и сообщений.
- **`src/claude_manager/config.py`** — загрузка `.env` и проверка обязательных настроек.
- **`src/claude_manager/claude_runner.py`** — запуск Claude Code CLI и чтение stream-json.
- **`src/claude_manager/process_manager.py`** — жизненный цикл процессов Claude, retry, `/stop`, progress.
- **`src/claude_manager/session_manager.py`** — привязка Telegram chat ID к active session.
- **`src/claude_manager/session_watcher.py`** — фоновая доставка новых сообщений из session-файлов.
- **`src/claude_manager/project_manager.py`** — поиск проектов и переключение рабочей директории.
- **`src/claude_manager/file_delivery.py`** и **`src/claude_manager/file_sender.py`** — обработка маркеров `[SEND_FILE:path]` и `[SHOW_FILE:path]`.

## Важные инварианты

- Бот локальный и рассчитан на одного человека. Несколько `ALLOWED_USER_IDS` допустимы только как устройства одного владельца.
- `.env` нельзя коммитить, архивировать или пересылать.
- `sessions.json`, `daily_sessions.json`, `received_files/` и Telethon session-файлы нельзя передавать другому пользователю.
- Claude Code CLI должен быть доступен в `PATH`, особенно при запуске через launchd.
- Скрипты `start-claude-manager.sh` и `restart-claude-manager.sh` содержат абсолютные пути текущего владельца. Новый пользователь должен заменить `/Users/ivan/Desktop/claude-sandbox/claude_manager` на свой путь или адаптировать launchd-настройку по `dev/docs/bot-launch-infrastructure.md`.
- Если проект лежит в macOS Desktop/iCloud-зоне, venv лучше держать вне Desktop, например в `~/.venvs/claude-manager/`, чтобы избежать TCC/provenance проблем.

## Проверки после правок

Базовая проверка для большинства изменений:

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

Если изменение затрагивает Telegram-интеграцию, файлы, session watcher или команды бота, дополнительно проверить соответствующие integration-тесты:

```bash
python -m pytest tests/integration/ -v
```

Если изменение затрагивает реальный Telegram-flow, E2E тесты запускать только после настройки Telethon по `dev/docs/deployment-guide.md`:

```bash
python tests/e2e/check_connection.py test
```

## Следующие шаги

- Новый владелец создаёт собственный `.env`.
- Новый владелец решает, нужен ли launchd-автозапуск. Если нужен — адаптирует пути в shell-скриптах и plist.
- Если новый владелец хочет использовать Claude/Codex pipeline-скиллы проекта, он отдельно восстанавливает shared-skills и mirror-sync по правилам в `CLAUDE.md` и `AGENTS.md`.
