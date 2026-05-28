# Инфраструктура запуска бота

Справочник по всем компонентам, которые участвуют в запуске и работе Claude Manager на Linux. Описывает где каждый компонент лежит, зачем он там, как компоненты связаны между собой и как их чинить.

## Где живёт код и состояние

Проект лежит в `/home/ivan/claude-sandbox/claude_manager/`. Venv — в `.venv/` внутри проекта (на Linux нет macOS-овской TCC-защищённой Desktop-зоны, поэтому venv можно держать рядом с кодом без проблем с editable install).

### В проекте

- **`src/claude_manager/`** — продакшен-код бота.
- **`.env`** — секреты (токен, ID пользователей) — в `.gitignore`.
- **`.venv/`** — виртуальное окружение Python с editable install (`pip install -e ".[dev]"`).
- **`restart-claude-manager.sh`** — скрипт безопасного рестарта (preflight + systemctl + post-flight).
- **`watch_and_restart.sh`** — наблюдатель для разработки (inotifywait + systemctl restart).
- **`sessions.json`, `daily_sessions.json`** — файлы состояния (привязки чат ↔ сессия, дневные номера).

### Вне проекта

- **`~/.config/systemd/user/claude-manager.service`** — unit-файл systemd для автозапуска.
- **`~/.local/state/claude-manager/claude-manager.log`** — основной лог (RotatingFileHandler, XDG-стандарт).
- **`~/.claude-manager.lock`** — файловая блокировка от двойного запуска (fcntl.flock).
- **`~/.claude-manager-current-project`** — последний выбранный проект (восстанавливается при старте).
- **`~/.claude-manager-silence-mode`** — состояние silence mode (persisted между перезапусками).
- **`~/.claude-manager-current-backend`** — выбранный CLI-бэкенд для новых сессий.
- **journalctl** — stdout/stderr бота под systemd попадают в `journalctl --user -u claude-manager.service`.

## systemd user service

Файл: `~/.config/systemd/user/claude-manager.service`.

Текущее содержимое:

```ini
[Unit]
Description=Claude Manager Telegram bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ivan/claude-sandbox/claude_manager
Environment=PATH=/home/ivan/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/home/ivan/claude-sandbox/claude_manager/.venv/bin/claude-manager
Restart=always
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

Ключевые поля:

- **`ExecStart`** — entry point `claude-manager`, поставляемый pip-овым editable install из `pyproject.toml`. На прямой запуск Python через `python -m` не переходим — entry point чище и не требует костылей с `sys.path`.
- **`Restart=always`** + **`RestartSec=10`** — systemd сам перезапускает бота при падении через 10 секунд. Раньше эту роль выполняла shell-обёртка с retry-логикой; на Linux она не нужна.
- **`TimeoutStopSec=30`** — даёт боту до 30 секунд на корректное завершение после SIGTERM (важно для самоперезапуска через `/restart`).
- **`Type=simple`** — stdout/stderr автоматически попадают в `journalctl`.
- **`Environment=PATH=...`** — обязательно включить путь к `node` для Claude CLI (это Node-приложение). Без этого Claude CLI падает с `env: node: No such file or directory`.

## Цепочка запуска

```
systemd (user)
  → ExecStart=/home/ivan/claude-sandbox/claude_manager/.venv/bin/claude-manager
    → claude_manager.main:main() (entry point из pyproject.toml)
      → файловая блокировка через fcntl
      → загрузка config, восстановление сессий
      → Telegram polling
```

## Самоперезапуск через `/restart`

Команда `/restart` в Telegram запускает отвязанный subprocess:

```
бот → asyncio.create_subprocess_exec("bash", "-c",
        "sleep 2 && systemctl --user restart claude-manager.service",
        start_new_session=True, stdout=DEVNULL, stderr=DEVNULL)
```

Через 2 секунды subprocess (отвязанный от cgroup сервиса через `start_new_session=True`) выполняет `systemctl --user restart`. systemd шлёт SIGTERM текущему процессу бота, ждёт до 30 секунд (`TimeoutStopSec=30`), запускает новый процесс через `ExecStart`. В `post_init` нового процесса бот читает маркер-файл `/tmp/claude-manager-restart-chat-id`, шлёт пользователю «Перезапустился, готов к работе», удаляет маркер.

## Внешний рестарт: `restart-claude-manager.sh`

Скрипт в корне проекта. Безопасен для запуска из терминала или внешнего агента, **не** из подпроцесса самого бота (бот убьёт собственное дерево — exit 137).

Поток:

1. **Preflight** — `check_editable_install` проверяет наличие `.venv/bin/claude-manager` и успешность `import claude_manager`. При ошибке — exit 1 + подсказка по починке.
2. **Restart** — `systemctl --user restart claude-manager.service`.
3. **Post-flight** — 3 попытки с интервалом 5 секунд. Успех = `systemctl --user is-active` + `pgrep -f claude_manager`. При провале — диагностика через `tail` лога и `journalctl`, exit 1.

Хелперы (`check_editable_install`, `service_is_running`, `print_diagnostics_on_failure`) вынесены в отдельные функции и покрыты юнит-тестами в `tests/test_restart_claude_manager_script.py`.

## Watcher для разработки: `watch_and_restart.sh`

Использует `inotifywait` из пакета `inotify-tools`. При изменении любого `.py`-файла в `src/` шлёт `systemctl --user restart claude-manager.service`. Дебаунс 1 секунда защищает от шквала событий при сохранении нескольких файлов одновременно в IDE.

Зависимость: `sudo apt install inotify-tools` (Debian/Ubuntu) или `sudo dnf install inotify-tools` (Fedora). Скрипт проверяет наличие команды на старте.

## Пересоздание venv

```bash
cd /home/ivan/claude-sandbox/claude_manager
rm -rf .venv
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Проверка: entry point на месте, импорт проходит
ls .venv/bin/claude-manager
python -c "import claude_manager; print('import OK')"
```

После пересоздания обязательно прогнать `./restart-claude-manager.sh` для проверки. Бот может работать «по инерции» (живой процесс держит модуль в памяти), но при первом рестарте упадёт с `No module named claude_manager`, если editable install не прошёл.

## Диагностика

### Бот не запускается

```bash
# 1. Статус сервиса
systemctl --user status claude-manager.service

# 2. Последние строки journalctl (stdout/stderr под systemd)
journalctl --user -u claude-manager.service -n 50 --no-pager

# 3. Лог приложения (RotatingFileHandler)
tail -50 ~/.local/state/claude-manager/claude-manager.log

# 4. Проверить editable install
source .venv/bin/activate
python -c "import claude_manager; print('OK')"

# 5. Проверить, что entry point на месте
ls -l .venv/bin/claude-manager
```

### Сервис в состоянии `failed`

systemd считает сервис «провалившимся», когда `Restart=always` исчерпал retry-budget. Сбросить статус:

```bash
systemctl --user reset-failed claude-manager.service
systemctl --user start claude-manager.service
```

### `claude-manager: command not found` в journalctl

Editable install сломан — entry point из `pyproject.toml` не зарегистрирован. Пересоздать venv (см. выше).

### Бот запущен в двух экземплярах

Защита: файловая блокировка `~/.claude-manager.lock` через `fcntl.flock`. Вторая копия (например, ручной `python -m claude_manager` параллельно с systemd-сервисом) сразу завершится с понятным сообщением.

## Связанные документы

- `deployment-guide.md` — пошаговая установка от нуля.
- `dev/docs/specs/28.05_17.25-linux-bot-commands-cleanup-spec.md` — спека миграции с macOS на Linux.
- `CLAUDE.md`, секции «Принципы эксплуатации» и «Команды разработки».
