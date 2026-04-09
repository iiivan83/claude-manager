# Сессия 30-03: Исправление запуска бота и настройка автозапуска

## Резюме

Исправлены два бага, блокировавших запуск бота (конфликт event loop, пустой PATH для Claude CLI). Старый бот из su-main-master 2 остановлен и перенесён в deprecated/. Новый бот запущен и настроен на автозапуск через macOS LaunchAgents.

## Изменённые файлы

- `src/claude_manager/main.py` — исправлен: убран asyncio.run() + async, _run_bot() стал синхронным, run_polling() сам управляет event loop. Удалена _restore_state() — логика перенесена в bot.py post_init
- `src/claude_manager/bot.py` — исправлен: setup_bot() из async в sync, post_init() расширен — теперь включает _clean_old_received_files() + восстановление привязок сессий (бывшая _restore_state)
- `src/claude_manager/claude_runner.py` — исправлен: импорт `from config import WORKING_DIR` заменён на `from claude_manager import config` + `config.WORKING_DIR` (фикс пустой строки при позднем вызове load_config). CLAUDE_CLI_COMMAND теперь использует shutil.which("claude") с фоллбэком на /usr/local/bin/claude
- `tests/test_main.py` — обновлён: удалён TestRestoreState (логика в bot.py), TestRunBot стал sync, моки asyncio.run заменены на моки _run_bot
- `tests/test_bot.py` — обновлён: TestSetupBot стал sync, TestPostInit расширен тремя тестами (восстановление привязок, ошибка восстановления)
- `tests/test_claude_runner.py` — обновлён: проверки `"claude" in args` заменены на `args[0].endswith("claude")` (полный путь)
- `~/Library/LaunchAgents/com.ivan.claude-manager.plist` — создан: автозапуск нового бота (RunAtLoad, KeepAlive, ThrottleInterval=10)
- `development/docs/deployment-guide.md` — обновлён: добавлен Шаг 6 (автозапуск через LaunchAgents) + строка в «Остановка бота»
- `CLAUDE.md` — обновлён: добавлены команды launchctl в «Команды разработки»

## Выполненные команды

- `kill 93304` — остановка старого бота (su-main-master 2/telegram-claude-bot/bot.py)
- `launchctl unload ~/Library/LaunchAgents/com.ivan.telegram-claude-bot.plist` — выгрузка старого автозапуска (выполнил пользователь)
- `mv .../telegram-claude-bot .../deprecated/` — перенос старого бота в deprecated/
- `mv .../com.ivan.telegram-claude-bot.plist .../deprecated/` — перенос старого plist туда же
- `launchctl load ~/Library/LaunchAgents/com.ivan.claude-manager.plist` — загрузка нового автозапуска (выполнил пользователь)

## Проблемы и решения

- **RuntimeError: This event loop is already running.** run_polling() создаёт свой event loop, но вызывался внутри asyncio.run(), который тоже создаёт loop. **Решение:** сделал _run_bot() и setup_bot() синхронными, перенёс async-инициализацию в post_init колбэк библиотеки python-telegram-bot.
- **FileNotFoundError: '' при запуске Claude CLI.** claude_runner.py делал `from config import WORKING_DIR` — копировал пустую строку на момент импорта (до вызова load_config()). **Решение:** заменил на `from claude_manager import config` + `config.WORKING_DIR` (обращение через модуль, читает актуальное значение).
- **Claude CLI не найден в PATH при nohup-запуске.** Процесс через nohup имеет урезанный PATH. **Решение:** CLAUDE_CLI_COMMAND = shutil.which("claude") or "/usr/local/bin/claude".
- **Старый бот воскресает после kill.** LaunchAgent с KeepAlive=true автоматически перезапускал процесс. **Решение:** launchctl unload для выгрузки сервиса.
- **bot.pid блокировка после kill.** Предыдущий процесс не успевал отпустить файл-замок. **Решение:** rm -f bot.pid перед повторным запуском.

## Решения

- **Старый бот перенесён, не удалён.** Папка telegram-claude-bot и plist-файл перемещены в /Users/ivan/Desktop/claude-sandbox/deprecated/. **Причина:** на случай если понадобится откатиться.
- **Восстановление состояния — в post_init, не в main.** _restore_state() перенесена из main.py в bot.py post_init(). **Причина:** post_init вызывается библиотекой после initialize() но до polling — правильное место для async-инициализации при sync-запуске через run_polling().

## Контекст для следующей сессии

**Бот работает** через macOS LaunchAgent (com.ivan.claude-manager). Рабочая директория Claude — `/Users/ivan/Desktop/claude-sandbox/su-main-master 2`. Логи: `~/Library/Logs/claude-manager.log`.

**Все 380 тестов зелёные** после всех исправлений.

**Коммитов не было** — все изменения только локальные.

**Старый бот** (su-main-master 2/telegram-claude-bot/) перенесён в `/Users/ivan/Desktop/claude-sandbox/deprecated/` вместе с plist-файлом.

**Незакрытые вопросы из предыдущей сессии:** фазы 5, 8, 9 пайплайна (E2E тестирование) — нужны Telethon-ключи. 6 warnings в тестах (RuntimeWarning: coroutine '_run_bot').
