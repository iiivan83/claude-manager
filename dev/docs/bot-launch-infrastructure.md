# Инфраструктура запуска бота

Справочник по всем компонентам, которые участвуют в запуске и работе Claude Manager. Описывает где каждый компонент лежит, зачем он там, как компоненты связаны между собой и как их чинить.

## Почему всё устроено именно так

Проект лежит в `~/Desktop/claude-sandbox/claude_manager/` — эта папка синхронизируется через iCloud, и это принципиально: код, документация и state-файлы доступны с другого компьютера для диагностики.

Но `~/Desktop` — TCC-защищённая директория macOS. TCC (Transparency, Consent, and Control — система разрешений macOS) создаёт две проблемы для фоновых процессов:

- **Провенанс и UF_HIDDEN.** APFS автоматически ставит `com.apple.provenance` на файлы внутри Desktop. Python 3.13 `site.py` (строки 177-180) считает файлы с `UF_HIDDEN` скрытыми и молча пропускает `.pth` файлы editable install → `import` падает с `ModuleNotFoundError`. `chflags`, `xattr -rc`, перемещение файлов — не помогают: APFS восстанавливает флаг из метаданных провенанса
- **TCC-блокировка launchd.** Процессы, запущенные через launchd, не имеют Full Disk Access к `~/Desktop` → `Operation not permitted` при попытке выполнить скрипт или даже сделать `getcwd` в этой директории

Решение — гибридная схема: код на Desktop (iCloud sync), runtime-артефакты вне Desktop.

## Карта компонентов

### В проекте (Desktop, синхронизируется через iCloud)

- **`src/claude_manager/`** — весь продакшен-код бота
- **`.env`** — секреты (токен, ID пользователей) — в `.gitignore`
- **`start-claude-manager.sh`** — reference-копия скрипта запуска. launchd её **не использует** — она здесь для iCloud-синхронизации и как справочник. При изменении логики запуска — обновить этот файл тоже
- **`.venv`** — симлинк на `~/.venvs/claude-manager/`. Все команды (`source .venv/bin/activate`, `pip install -e .`) работают через симлинк прозрачно
- **`sessions.json`**, **`daily_sessions.json`** — файлы состояния, синхронизируются для удалённой диагностики

### Вне проекта (вне Desktop, не синхронизируется)

- **`~/.venvs/claude-manager/`** — виртуальное окружение Python. Здесь потому что:
  - Нет `com.apple.provenance` → нет `UF_HIDDEN` → `.pth` файлы работают
  - Не гоняет гигабайты платформо-специфичных бинарников через iCloud
  - Симлинк `.venv` в проекте обеспечивает совместимость
- **`~/.local/bin/start-claude-manager.sh`** — рабочий скрипт запуска для launchd. Здесь потому что:
  - Нет TCC-блокировки — launchd может его запустить
  - Нет `com.apple.provenance` на скрипте (создан вне Desktop)
- **`~/Library/LaunchAgents/com.ivan.claude-manager.plist`** — конфигурация автозапуска macOS
- **`~/Library/Logs/claude-manager.log`** — stdout бота
- **`~/Library/Logs/claude-manager.error.log`** — stderr бота (здесь видны Python-крэши и TCC-ошибки)
- **`~/.claude-manager.lock`** — файловая блокировка от двойного запуска (fcntl.flock)
- **`~/.claude-manager-current-project`** — последний выбранный проект (восстанавливается при старте)
- **`~/.claude-manager-silence-mode`** — состояние silence mode

## Цепочка запуска

```
launchd (macOS)
  → ~/.local/bin/start-claude-manager.sh  (bash-обёртка с retry-логикой)
    → ~/.venvs/claude-manager/bin/python  (Python из venv вне Desktop)
      → sys.path.insert(0, ".../src")     (обход UF_HIDDEN на .pth)
        → runpy._run_module_as_main("claude_manager")
          → claude_manager.main.main()    (файловая блокировка, polling)
```

Скрипт запуска делает `cd /Users/ivan/Desktop/claude-sandbox/claude_manager` перед вызовом Python — рабочая директория процесса бота всегда проектная.

### Retry-логика в скрипте запуска

Python 3.13 может крэшиться при инициализации (`InterruptedError` в `getpath.py` — PEP 475 ещё не активен на стадии `core initialized`). Скрипт:

1. Запускает Python, замеряет время работы
2. Если крэш в первые 5 секунд — считает startup crash, retry (до 3 попыток с паузой 10 секунд)
3. Если Python работал дольше 5 секунд — это не startup crash, выходит с его exit code. launchd перезапустит через `KeepAlive`
4. Если все 3 попытки исчерпаны — отправляет уведомление в Telegram (или macOS notification как fallback)

## launchd plist

Файл: `~/Library/LaunchAgents/com.ivan.claude-manager.plist`

Ключевые поля:
- **`ProgramArguments`** → `~/.local/bin/start-claude-manager.sh` (НЕ проектная копия)
- **`WorkingDirectory`** → `/Users/ivan` (НЕ Desktop — иначе `getcwd` вернёт `Operation not permitted`)
- **`KeepAlive`** → `true` — launchd перезапускает бота при падении
- **`ThrottleInterval`** → `60` — минимальная пауза между перезапусками (секунды)
- **`RunAtLoad`** → `true` — запускать при загрузке
- **`PATH`** в `EnvironmentVariables` — обязательно включить путь к `node` (Claude CLI = Node.js-приложение)

## Пересоздание venv

При обновлении Python или повреждении venv:

```bash
# ВАЖНО: создавать в ~/.venvs/, НЕ в проекте
rm -rf ~/.venvs/claude-manager
python3.13 -m venv ~/.venvs/claude-manager

# Симлинк уже на месте (.venv → ~/.venvs/claude-manager)
source .venv/bin/activate
pip install -e ".[dev]"

# Проверка: флаги должны быть 0x0, импорт должен пройти
python -c "
import os, stat
s = os.stat('.venv/lib/python3.13/site-packages/__editable__.claude_manager-0.1.0.pth')
print('flags:', hex(s.st_flags), '— OK' if s.st_flags == 0 else '— ПРОБЛЕМА: UF_HIDDEN')
"
python -c "import claude_manager; print('import OK')"
```

Если симлинк потерялся: `ln -s ~/.venvs/claude-manager .venv`

## Обновление скрипта запуска

При изменении логики запуска (retry, пути, уведомления):

1. Отредактировать `start-claude-manager.sh` в проекте (reference-копия)
2. Скопировать в `~/.local/bin/`: `cp start-claude-manager.sh ~/.local/bin/start-claude-manager.sh`
3. Сделать исполняемым: `chmod +x ~/.local/bin/start-claude-manager.sh`
4. Перезагрузить launchd: `launchctl unload ~/Library/LaunchAgents/com.ivan.claude-manager.plist && launchctl load ~/Library/LaunchAgents/com.ivan.claude-manager.plist`

## Диагностика проблем

### Бот не запускается

```bash
# 1. Проверить статус launchd
launchctl list | grep claude-manager
# PID  Exit  Label
# 1234  0    com.ivan.claude-manager  ← работает (PID есть, exit 0)
# -     126  com.ivan.claude-manager  ← не работает (PID нет, exit 126)

# 2. Проверить error-лог (здесь видны TCC-ошибки, Python-крэши)
tail -20 ~/Library/Logs/claude-manager.error.log

# 3. Проверить что Python может импортировать модуль
source .venv/bin/activate
python -c "import claude_manager; print('OK')"

# 4. Проверить UF_HIDDEN на .pth (должно быть 0x0)
python -c "
import os, stat
s = os.stat('.venv/lib/python3.13/site-packages/__editable__.claude_manager-0.1.0.pth')
print(hex(s.st_flags))
"
```

### `Operation not permitted` в error-логе

Launchd не может получить доступ к Desktop. Проверить:
- `ProgramArguments` указывает на `~/.local/bin/start-claude-manager.sh` (не на проектную копию)
- `WorkingDirectory` — `/Users/ivan` (не Desktop)

### `ModuleNotFoundError: No module named 'claude_manager'`

UF_HIDDEN на `.pth` файле. Проверить флаги (см. выше). Если `0x8040` — venv внутри TCC-зоны, нужно пересоздать в `~/.venvs/`.

### `Fatal Python error: error evaluating path`

Python 3.13 startup crash (InterruptedError). Transient — скрипт запуска делает retry автоматически. Если все 3 попытки провалились — придёт уведомление в Telegram. Обычно помогает подождать и перезапустить: `launchctl kickstart -k "gui/$(id -u)/com.ivan.claude-manager"`

### Бот работал, но перестал после обновления macOS

macOS может сбросить TCC-разрешения при обновлении. Проверить error-лог на `Operation not permitted`. Если есть — убедиться что скрипт запуска лежит в `~/.local/bin/` (не в Desktop) и plist указывает на него.

## Связанные документы

- [deployment-guide.md](deployment-guide.md) — пошаговая установка от нуля
- [dev/docs/adr/03.05_10.58-...-venv-launchd-migration-out-of-desktop.md](adr/03.05_10.58-session-change-documenter-venv-launchd-migration-out-of-desktop.md) — ADR с историей решения и альтернативами
- CLAUDE.md, секции «Принцип изоляции venv от TCC-защищённых директорий» и «Принцип изоляции скрипта запуска от TCC-зоны»
