# Сессия 10-04: переключение рабочей директории бота с budgets на claude_manager

## Резюме

Пользователь попросил сделать так, чтобы бот работал из проекта `claude_manager` вместо `budgets`. Переключено через изменение одной переменной в `.env` + перезапуск процесса через LaunchAgent. Бот поднялся с новой конфигурацией, подтверждено по логам.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `.env` | изменён | `CLAUDE_WORKING_DIR` переключен с `/Users/ivan/Desktop/claude-sandbox/budgets` на `/Users/ivan/Desktop/claude-sandbox/claude_manager` (строка 9) |

## Выполненные команды

- `kill 2150` — отправка SIGTERM старому процессу бота. LaunchAgent с `KeepAlive=true` автоматически поднял новый процесс (PID 82370) через `ThrottleInterval=10` секунд
- `tail -30 /Users/ivan/Library/Logs/claude-manager.error.log` — проверка что новый процесс стартовал с обновлённой конфигурацией

## Решения

- **Переключение через `.env`, а не через смену CWD процесса.** **Причина**: все файлы состояния бота (`sessions.json`, `daily_sessions.json`, `received_files/`) формируются от `config.WORKING_DIR`, а не от CWD процесса Python. См. `src/claude_manager/session_manager.py:192` (`_bindings_path = Path(config.WORKING_DIR) / BINDINGS_FILENAME`), `src/claude_manager/daily_session_registry.py:195`, `src/claude_manager/bot.py:159`. `WorkingDirectory` в `~/Library/LaunchAgents/com.ivan.claude-manager.plist` = CWD процесса Python и на бизнес-логику не влияет — там всегда должен оставаться `claude_manager`, потому что там `.venv`.
- **Перезапуск через `kill`, а не `launchctl`.** **Причина**: в sandbox не было прав на `launchctl`, а `KeepAlive=true` в plist делает `kill` эквивалентным корректному перезапуску.
- **Старые файлы `sessions.json` и `daily_sessions.json` в `budgets/` оставлены как архив.** **Причина**: перенос бесполезен — session_id в них ссылаются на сессии Claude CLI в `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-budgets/`, после смены рабочей директории бот ищет сессии в `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude_manager/`, старые ID не найдёт. Чистый старт — правильный путь.

## Проблемы и решения

- **Проблема**: Сначала не уточнил направление переключения и начал идти в сторону `budgets` (решив, что раз файл из `budgets` открыт в IDE — значит туда). **Решение**: Пользователь прервал и уточнил — нужно `claude_manager`.
- **Проблема**: После перезапуска в `claude-manager.error.log` каждые 2 секунды сыпется `WARNING session_reader: Папка сессий не найдена: /Users/ivan/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude_manager`. **Решение**: Не бага — это `session_watcher` опрашивает папку Claude CLI каждые 2 секунды, а папка появится только после первого запуска Claude CLI ботом (когда пользователь отправит первое сообщение). Warnings исчезнут автоматически.

## Незавершённое

- [ ] Пользователь ещё не протестировал работу в Telegram — надо отправить сообщение боту, убедиться что Claude отвечает и работает именно в контексте `claude_manager` (видит `CLAUDE.md`, скиллы, код проекта)
- [ ] Файл `/Users/ivan/Library/Logs/claude-manager.error.log` разросся до 314 МБ (за месяц работы + каждые 2 сек сейчас пишется WARNING про ненайденную папку). Стоит почистить/настроить ротацию. Не трогал без явного запроса.

## Контекст для следующей сессии

- **Рабочая директория бота теперь `claude_manager`** — это видно в строке логов `[INFO] main: Рабочая директория: /Users/ivan/Desktop/claude-sandbox/claude_manager` в 15:40:38
- **Текущий PID бота: 82370** (запущен через LaunchAgent `com.ivan.claude-manager`)
- **Файлы `sessions.json` и `daily_sessions.json` в `claude_manager/` ещё не созданы** — появятся при первом сообщении от пользователя. Сейчас бот в режиме `/all` (мониторинг), привязок нет
- **Папка `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude_manager/` не существует** — создастся при первом запуске Claude CLI ботом. До этого момента session_watcher будет логировать warnings каждые 2 секунды
- **Старые файлы `sessions.json` и `daily_sessions.json` в `/Users/ivan/Desktop/claude-sandbox/budgets/` не тронуты** — если понадобится вернуться к проекту `budgets`, достаточно вернуть `CLAUDE_WORKING_DIR` в `.env` на старое значение и перезапустить бота
- **Архитектурный факт для запоминания**: `config.WORKING_DIR` — единая «папка проекта Claude» для бота. Это одновременно `cwd` для процесса Claude CLI (в `src/claude_manager/claude_runner.py:214`) и хранилище состояния бота. `WorkingDirectory` в LaunchAgent plist — это отдельная штука, CWD процесса Python, не имеет отношения к бизнес-логике бота

## Поправка 10-04 16:02

В разделе «Проблемы и решения» зафиксирована ошибочная диагностика про warning «Папка сессий не найдена». Было написано, что это не бага — мол, папка появится после первого запуска Claude CLI. На самом деле это **корневой баг** в `session_reader._encode_project_path` — функция неверно формирует имя папки из пути с подчёркиванием. Полный разбор — в `dev/docs/logs/root-cause-reports/10-04_16-02_session-reader-path-encoding.md`.

Оригинальный текст отчёта оставлен как историческая запись. Процессный урок: не принимать дружелюбные объяснения симптомов без эмпирической проверки. Минимум — `ls ~/.claude/projects/` и сверка реальных папок с результатом функции-кодировщика.
