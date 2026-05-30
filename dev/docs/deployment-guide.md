# Гайд по запуску Claude Manager

Пошаговая инструкция: от чистой Linux-системы до работающего бота.

## Что нужно заранее

- **Linux** с systemd (Ubuntu 22+, Debian 12+, Fedora 38+, Arch — стандартные дистрибутивы). Бот использует `systemd --user` для автозапуска и `inotify-tools` для watcher разработки.
- **Python 3.13** — `python3.13 --version`. Если в системе нет — установить через `apt install python3.13` или `pyenv`.
- **Claude Code CLI** — `claude --version` должна работать в терминале. Бот общается с этим CLI через stream-json.
- **Telegram-бот** — создать через @BotFather в Telegram, получить токен.

## Шаг 1: Установка

```bash
# Клонировать репозиторий
git clone <url-репозитория>
cd claude_manager

# Создать виртуальное окружение
python3.13 -m venv .venv

# Активировать
source .venv/bin/activate

# Установить зависимости + editable install (entry point claude-manager)
pip install -e ".[dev]"

# Проверить, что модуль импортируется и entry point на месте
python -c "import claude_manager; print('OK')"
ls .venv/bin/claude-manager
```

**Ожидаемый результат:** `import OK` и видим путь `.venv/bin/claude-manager`.

**Важно:** `pip install -e .` обязателен — без editable install systemd не сможет запустить бота (`claude-manager` отсутствует в `.venv/bin/`).

## Шаг 2: Настройка `.env`

```bash
cp .env.example .env
```

Открыть `.env` в редакторе, заполнить:

- **TELEGRAM_BOT_TOKEN** — токен от @BotFather (длинная строка вида `123456:ABC-DEF1234...`).
- **ALLOWED_USER_IDS** — ваш Telegram-ID (узнать через @userinfobot). Несколько ID через запятую, но только для одного человека с разных устройств.
- **PROJECTS_ROOT_DIR** — корневая папка со всеми проектами для команды `/projects`. Обязательная переменная.

Необязательное:

- **CLAUDE_WORKING_DIR** — рабочая папка по умолчанию для Claude. Без указания — текущая папка.
- **E2E_TEST_USER_ID** — для E2E-тестов через Telethon. **НЕ** добавлять в `ALLOWED_USER_IDS` — сломает однопользовательский инвариант.

Пример:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ALLOWED_USER_IDS=123456789
PROJECTS_ROOT_DIR=/home/ivan/projects
```

## Шаг 3: Первый запуск вручную

```bash
source .venv/bin/activate
python -m claude_manager
```

**Ожидаемый результат:** в консоли строки:

```
[INFO] claude_manager.config: Конфигурация загружена: рабочая директория=..., корень проектов=..., пользователей в белом списке=1
[INFO] claude_manager.main: Claude Manager запускается...
```

Бот ждёт сообщений из Telegram. Остановить — `Ctrl+C`.

## Шаг 4: Проверка через Telegram

1. Открыть бота в Telegram (имя задано при создании через @BotFather).
2. Отправить `/new` — бот должен ответить «Создана новая сессия #1».
3. Отправить любое текстовое сообщение — бот передаст в Claude и пришлёт ответ.

Если бот не отвечает — проверить консоль на ошибки авторизации (`Неавторизованный доступ` означает, что ID не в `ALLOWED_USER_IDS`).

## Шаг 5: Автозапуск через systemd

Чтобы бот запускался при входе в систему и перезапускался при падении, используем `systemd --user`.

### Установка unit-файла

```bash
# Создать папку для user-юнитов (если её нет)
mkdir -p ~/.config/systemd/user

# Скопировать unit-файл
cat > ~/.config/systemd/user/claude-manager.service <<'EOF'
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
EOF
```

**Важно:** подправить `WorkingDirectory`, `Environment=PATH=` и `ExecStart` под свои пути. `PATH` должен содержать путь к `node` (узнать через `which node`) — Claude CLI это Node-приложение.

### Перезагрузить systemd и включить автозапуск

```bash
systemctl --user daemon-reload
systemctl --user enable --now claude-manager.service
```

`--now` запускает сервис сразу же, `enable` включает в автозапуск.

### Проверка статуса

```bash
systemctl --user status claude-manager.service
```

Должно быть `active (running)`. Если `failed` — проверить логи:

```bash
journalctl --user -u claude-manager.service -n 50 --no-pager
tail -50 ~/.local/state/claude-manager/claude-manager.log
```

### Команды управления

```bash
systemctl --user start claude-manager.service     # запустить
systemctl --user stop claude-manager.service      # остановить
systemctl --user restart claude-manager.service   # рестарт
systemctl --user disable claude-manager.service   # убрать из автозапуска
systemctl --user status claude-manager.service    # статус
```

Чтобы сервис продолжал работать после выхода пользователя (без активной сессии в TTY) — включить linger:

```bash
sudo loginctl enable-linger $USER
```

## Шаг 6: Запуск с автоперезапуском (для разработки)

Watcher следит за `.py`-файлами в `src/` и шлёт `systemctl --user restart` при изменениях.

```bash
# Установить inotify-tools (один раз)
sudo apt install inotify-tools      # Debian/Ubuntu
sudo dnf install inotify-tools      # Fedora/RHEL

# Сделать скрипт исполняемым (один раз)
chmod +x watch_and_restart.sh

# Запустить
./watch_and_restart.sh
```

Дебаунс 1 секунда защищает от шквала рестартов при сохранении нескольких файлов в IDE. Остановить — `Ctrl+C`.

## Шаг 7: Безопасный рестарт из терминала

`restart-claude-manager.sh` делает preflight (editable install здоров?), `systemctl restart`, post-flight (сервис активен + Python-процесс запущен?). Показывает диагностику при провале.

```bash
chmod +x restart-claude-manager.sh   # один раз
./restart-claude-manager.sh
```

**НЕ запускать из подпроцесса самого бота** (например, через Claude Code Bash tool) — скрипт убьёт собственное дерево процессов, exit 137. Для самоперезапуска — команда `/restart` в Telegram.

## Шаг 8: Настройка E2E-тестов (необязательно)

E2E-тесты через Telethon шлют боту реальные сообщения от тестового аккаунта.

Что нужно:
- Отдельный Telegram-аккаунт (не тот, с которого пишем боту).
- API-ключи Telegram: https://my.telegram.org → API development tools.

```bash
# Установить E2E-зависимости (уже включены в [dev])
pip install -e ".[dev]"
```

Добавить в `.env`:

```
TELETHON_API_ID=ваш_api_id
TELETHON_API_HASH=ваш_api_hash
TELETHON_PHONE=+номер_тестового_аккаунта
TELETHON_BOT_USERNAME=@имя_вашего_бота
E2E_TEST_USER_ID=id_тестового_аккаунта
```

**Важно:** `E2E_TEST_USER_ID` **не** должен совпадать с `ALLOWED_USER_IDS` — это сломает однопользовательский инвариант бота.

Авторизация Telethon (при первом запуске нужен SMS-код):

```bash
python tests/e2e/check_connection.py            # запрос SMS-кода
python tests/e2e/check_connection.py XXXXXX     # ввод кода
python tests/e2e/check_connection.py test       # проверка подключения
```

После успеха session-файл `tests/e2e/telethon_test.session` сохраняется — повторный ввод SMS не нужен.

## Остановка бота

- **Прямой запуск (`python -m claude_manager`):** `Ctrl+C`.
- **Через watcher:** `Ctrl+C` — остановит и watcher, и сервис не тронет (рестарт через systemctl, не через kill).
- **Под systemd:** `systemctl --user stop claude-manager.service`.

## Решение проблем

- **«TELEGRAM_BOT_TOKEN не задан»** — `.env` не найден или поле пустое.
- **«ALLOWED_USER_IDS не задан»** — заполнить поле в `.env`.
- **«PROJECTS_ROOT_DIR не задан»** — заполнить поле, путь к существующей папке.
- **«Бот уже запущен (файл bot.pid заблокирован другим процессом)»** — другая копия активна. Остановить через `systemctl --user stop` или найти процесс через `pgrep -f claude_manager`.
- **«env: node: No such file or directory» в journalctl** — `PATH` в unit-файле не включает путь к `node`. Узнать `which node`, добавить в `Environment=PATH=...`.
- **`claude-manager: command not found` при запуске сервиса** — editable install не сделан. Активировать venv и `pip install -e ".[dev]"`.
- **«Обнаружен конфликт: другой бот уже использует этот токен»** — где-то ещё запущена копия бота с тем же токеном.
