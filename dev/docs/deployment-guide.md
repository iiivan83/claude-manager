# Гайд по запуску Claude Manager

Пошаговая инструкция: от чистой системы до работающего бота.

## Что нужно заранее

- **macOS** — бот использует файловую блокировку через fcntl, которая работает только на macOS
- **Python 3.13** — язык программирования, на котором написан бот. Проверить версию: `python3 --version`
- **Claude Code CLI** — программа Claude, которая должна быть установлена и доступна из терминала. Проверить: `claude --version`
- **Telegram-бот** — нужно создать бота через @BotFather в Telegram и получить токен

## Шаг 1: Установка

Скачайте проект и установите зависимости.

```bash
# Клонируйте репозиторий (или скачайте архив)
git clone <url-репозитория>
cd claude_manager

# Создайте виртуальное окружение (изолированная среда для Python-пакетов)
python3.13 -m venv .venv

# Активируйте виртуальное окружение
source .venv/bin/activate

# Установите зависимости
pip install -r requirements.txt

# Обязательно: установите пакет в editable-режиме
# Без этого `python -m claude_manager` не найдёт модуль при перезапуске
pip install -e .

# Проверьте, что модуль импортируется
python -c "import claude_manager; print('OK')"
```

**Ожидаемый результат:** команда `pip install -e .` завершается без ошибок, проверка импорта выводит `OK`.

**Важно:** если вы пересоздаёте venv (`python3.13 -m venv .venv`) — `pip install -e .` нужно выполнить заново. Без этого бот может работать «по инерции» (живой процесс держит модуль в памяти), но упадёт при первом рестарте с ошибкой `No module named claude_manager`.

Если нужны тесты и E2E-инструменты:

```bash
pip install -e ".[dev]"
```

## Шаг 2: Настройка .env

Создайте файл с настройками на основе шаблона.

```bash
cp .env.example .env
```

Откройте файл `.env` в текстовом редакторе и заполните обязательные поля:

- **TELEGRAM_BOT_TOKEN** — токен бота, который выдал @BotFather. Выглядит как длинная строка из цифр и букв (например, `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
- **ALLOWED_USER_IDS** — ваш Telegram-ID (числовой идентификатор, не username). Узнать свой ID можно через бота @userinfobot в Telegram. Несколько ID через запятую: `123456789,987654321`

Необязательные поля:

- **CLAUDE_WORKING_DIR** — папка проекта, с которой Claude будет работать по умолчанию. Если не указана, используется текущая папка

Пример заполненного .env:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ALLOWED_USER_IDS=123456789
CLAUDE_WORKING_DIR=/Users/you/your-project
```

**Ожидаемый результат:** файл `.env` создан и содержит заполненные поля `TELEGRAM_BOT_TOKEN` и `ALLOWED_USER_IDS`.

## Шаг 3: Запуск

```bash
# Убедитесь, что виртуальное окружение активировано
source .venv/bin/activate

# Запустите бота
python -m claude_manager
```

**Ожидаемый результат:** в консоли появятся строки вида:

```
[INFO] claude_manager.config: Конфигурация загружена: рабочая директория=..., пользователей в белом списке=1
[INFO] claude_manager.main: Claude Manager запускается...
[INFO] claude_manager.main: Рабочая директория: /Users/...
```

Бот продолжает работать, ожидая сообщений из Telegram.

## Шаг 4: Проверка работы

1. Откройте Telegram и найдите своего бота (по имени, которое вы задали при создании через @BotFather)
2. Отправьте команду `/new` — бот должен ответить «Создана новая сессия #1»
3. Отправьте любое текстовое сообщение — бот передаст его в Claude Code и пришлёт ответ

Если бот не отвечает — проверьте консоль на наличие ошибок.

## Шаг 5: Запуск с автоперезапуском (для разработки)

Скрипт `watch_and_restart.sh` следит за изменениями в .py файлах и автоматически перезапускает бота при каждом сохранении.

```bash
# Сделайте скрипт исполняемым (нужно один раз)
chmod +x watch_and_restart.sh

# Запустите
./watch_and_restart.sh
```

**Ожидаемый результат:** в консоли видно `[INFO] Наблюдатель запущен` и `[INFO] Бот запущен`. При изменении любого .py файла в `src/claude_manager/` бот автоматически перезапустится.

Для остановки нажмите `Ctrl+C` — скрипт корректно завершит и наблюдатель, и бота.

## Шаг 6: Автозапуск (для постоянной работы)

Чтобы бот запускался автоматически при входе в систему и перезапускался при падении, используйте LaunchAgents — механизм автозапуска macOS.

Файл конфигурации: `~/Library/LaunchAgents/com.ivan.claude-manager.plist`

```bash
# Загрузить сервис (бот запустится сразу и будет запускаться при каждом входе в систему)
launchctl load ~/Library/LaunchAgents/com.ivan.claude-manager.plist

# Выгрузить сервис (остановить бота и убрать из автозапуска)
launchctl unload ~/Library/LaunchAgents/com.ivan.claude-manager.plist
```

**Обязательно:** plist-файл должен содержать `PATH` с путём к `node` — Claude CLI это Node.js-приложение (`#!/usr/bin/env node`), а LaunchAgent не наследует PATH из терминала. Без этого Claude CLI падает с ошибкой `env: node: No such file or directory`. Пример блока для plist:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key>
    <string>/usr/local/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
</dict>
```

Путь к node зависит от способа установки. Узнать: `which node`.

**Ожидаемый результат:** бот запускается автоматически. Проверить логи:

```bash
cat ~/Library/Logs/claude-manager.log
cat ~/Library/Logs/claude-manager.error.log
```

**Защита от дублей:** бот использует глобальный lock-файл `~/.claude-manager.lock`. Вторая копия (из любого источника — LaunchAgent, `watch_and_restart.sh`, ручной запуск) получит ошибку «Бот уже запущен» и не запустится.

## Остановка бота

- **При прямом запуске** (`python -m claude_manager`): нажмите `Ctrl+C` в терминале
- **При запуске через watch_and_restart.sh**: нажмите `Ctrl+C` — скрипт остановит и бота, и наблюдатель
- **При автозапуске через LaunchAgents**: `launchctl unload ~/Library/LaunchAgents/com.ivan.claude-manager.plist`

## Шаг 7: Настройка E2E тестирования (необязательно)

E2E тесты отправляют реальные сообщения боту через Telegram, используя библиотеку Telethon (подключается как обычный пользователь).

**Что нужно:**
- Отдельный Telegram-аккаунт для тестирования (не тот, с которого вы пишете боту)
- API-ключи Telegram — получить на https://my.telegram.org (раздел API development tools)

**Настройка:**

1. Установите E2E-зависимости: `pip install -e ".[dev]"`
2. Добавьте в `.env` переменные Telethon:
   ```
   TELETHON_API_ID=ваш_api_id
   TELETHON_API_HASH=ваш_api_hash
   TELETHON_PHONE=+номер_тестового_аккаунта
   TELETHON_BOT_USERNAME=@имя_вашего_бота
   ```
3. Добавьте Telegram-ID тестового аккаунта в отдельную переменную `E2E_TEST_USER_ID` (НЕ в `ALLOWED_USER_IDS` — это сломает однопользовательский инвариант бота):
   ```
   E2E_TEST_USER_ID=id_тестового_аккаунта
   ```
4. Перезапустите бота
5. Авторизуйте Telethon (при первом запуске нужен SMS-код):
   ```bash
   # Шаг 1: запросить SMS-код
   python tests/e2e/check_connection.py

   # Шаг 2: ввести код из SMS
   python tests/e2e/check_connection.py XXXXXX
   ```
6. Проверьте подключение: `python tests/e2e/check_connection.py test`

**Ожидаемый результат:** скрипт выводит «Успех! Бот работает. Ответ: Создана новая сессия #N».

После авторизации session-файл (`tests/e2e/telethon_test.session`) сохраняется — повторный ввод SMS-кода не нужен.

**Инфраструктура E2E:**
- `tests/e2e/test_client.py` — клиент-обёртка `TelegramTestClient` (send_message, wait_for_response, send_photo)
- `tests/e2e/check_connection.py` — скрипт проверки подключения
- E2E тесты исключены из обычного `pytest tests/` (запускаются отдельно)

## Решение проблем

- **«TELEGRAM_BOT_TOKEN не задан»** — файл `.env` не найден или поле `TELEGRAM_BOT_TOKEN` пустое. Проверьте, что `.env` лежит в корне проекта и содержит токен

- **«ALLOWED_USER_IDS не задан»** — поле `ALLOWED_USER_IDS` в `.env` пустое. Укажите ваш числовой Telegram-ID

- **«Бот уже запущен (файл bot.pid заблокирован другим процессом)»** — другая копия бота уже работает. Найдите и остановите её, или удалите файл `~/.claude-manager.lock`

- **«Claude Code CLI не найден»** — программа `claude` не установлена или недоступна в PATH. Установите Claude Code CLI и убедитесь, что команда `claude --version` работает в терминале

- **«env: node: No such file or directory»** — Claude CLI не находит `node`. При запуске через LaunchAgent нужно добавить PATH с путём к node в plist (см. Шаг 6)

- **Бот не отвечает на сообщения** — проверьте, что ваш Telegram-ID добавлен в `ALLOWED_USER_IDS`. Посмотрите логи в консоли — там будет сообщение «Неавторизованный доступ», если ID не в белом списке

- **«Обнаружен конфликт: другой бот уже использует этот токен»** — где-то ещё запущен бот с тем же токеном (например, на другом компьютере или в другом терминале). Остановите все другие экземпляры

- **«Fatal Python error: error evaluating path» + «InterruptedError: [Errno 4]»** — Python 3.13 крэшится при инициализации из-за прерванного системного вызова (EINTR) в модуле `getpath.py`. Происходит, когда сигнал (вероятно от Spotlight или launchd) попадает в узкое окно до активации PEP 475. Проблема transient — обычно проходит сама через 60-80 минут. Диагностика: `grep "Fatal Python error" ~/Library/Logs/claude-manager.error.log`. Быстрый workaround: `launchctl kickstart -k "gui/$(id -u)/com.ivan.claude-manager"` — попробовать несколько раз с интервалом в пару минут. Обёртка `start-claude-manager.sh` автоматически делает до 3 retry при таких крэшах
