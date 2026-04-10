# Сессия 30-03: fix-multi-instance-and-node-path

## Резюме

Исправлены две критические проблемы: множественные копии бота запускались одновременно (глобальный lock-файл), и Claude CLI не работал при запуске через LaunchAgent (PATH к node). Создан E2E тест на утечку ответов из чужих сессий.

## Изменённые файлы

- `src/claude_manager/main.py` — изменён: lock-файл перенесён из `config.WORKING_DIR/bot.pid` в глобальный `~/.claude-manager.lock`. Константа `LOCK_FILENAME` заменена на `LOCK_PATH`
- `src/claude_manager/process_manager.py` — изменён: добавлена функция `_read_stderr()` — читает stderr процесса Claude CLI при падении. В `_process_events()` stderr теперь логируется и показывается пользователю
- `tests/e2e/test_session_flow.py` — изменён: добавлен `test_flow05_new_session_no_ghost_responses` — E2E тест на утечку ответов из чужих сессий после `/new`. Добавлен `import asyncio`
- `tests/test_main.py` — изменён: все тесты `TestAcquireLock` и `TestEdgeCases` обновлены для работы с `LOCK_PATH` вместо `LOCK_FILENAME` + `config.WORKING_DIR`. Добавлен `test_lock_uses_global_path_not_working_dir`
- `watch_and_restart.sh` — изменён: `PID_FILE` переведён на `$HOME/.claude-manager.lock` (единый путь с Python-кодом)
- `~/Library/LaunchAgents/com.ivan.claude-manager.plist` — изменён: добавлен блок `EnvironmentVariables` с `PATH`, включающим `/usr/local/opt/node@22/bin`
- `development/docs/session-reports/30-03/13-34_autofix-e2e-results.md` — создан: отчёт autofix-e2e (все E2E прошли, баг утечки не воспроизвёлся)

## Коммиты

- `dd57bd9` — fix(main): глобальный lock-файл — блокировка множественных копий бота

## Выполненные команды

- `python -m pytest tests/ -v --ignore=tests/e2e` — регрессионные прогоны (384→385 тестов, все PASS)
- `python -m pytest tests/e2e/test_session_flow.py -v` — E2E прогоны (6/6 PASS при одном экземпляре бота)
- `python -m claude_manager` — проверка блокировки: вторая копия бота корректно завершается с «Бот уже запущен»
- `launchctl unload/load` — перезагрузка LaunchAgent после добавления PATH

## Решения

- **Глобальный lock vs убийство старого процесса при запуске**: выбран глобальный lock (`~/.claude-manager.lock`). Причина: одна строка кода вместо целого механизма, надёжнее (не зависит от PID-файлов и race conditions).
- **stderr из Claude CLI**: добавлено чтение stderr при падении процесса. Причина: без этого ошибка `env: node: No such file or directory` была невидима — бот показывал пустую строку ошибки.

## Проблемы и решения

- **Проблема**: при создании новой сессии ответы приходили от чужих сессий (например #42). **Решение**: первопричина — 3-4 копии бота работали одновременно, каждая получала случайные сообщения от Telegram. Глобальный lock предотвращает запуск дублей.
- **Проблема**: тест `test_flow01` падал — после переподключения к сессии через `/N` бот отвечал «Вы в режиме мониторинга». **Решение**: та же причина — множественные экземпляры. При одном экземпляре тест проходит стабильно.
- **Проблема**: Claude CLI мгновенно умирал при запуске через LaunchAgent (`env: node: No such file or directory`). **Решение**: добавлен `PATH` с `/usr/local/opt/node@22/bin` в `EnvironmentVariables` plist-файла LaunchAgent.
- **Проблема**: файловая блокировка `fcntl.flock` не предотвращала множественные экземпляры. **Решение**: lock-файл был привязан к `WORKING_DIR` — разные источники запуска (LaunchAgent, watch_and_restart.sh, ручной) создавали lock в разных папках. Перенос на фиксированный `~/.claude-manager.lock` решил проблему.

## Незавершённое

- [ ] Коммит для `process_manager.py` (stderr чтение) и `plist` (PATH) — изменения есть, но не закоммичены
- [ ] Реестр дневных сессий содержит ~50 записей, watcher каждые 2 сек пытается читать несуществующие JSONL-файлы — массовые WARNING в логах. Стоит почистить реестр или добавить фильтрацию

## Контекст для следующей сессии

- Бот работает через LaunchAgent с `KeepAlive: true` — при падении перезапускается автоматически
- Глобальный lock `~/.claude-manager.lock` предотвращает дубли. `watch_and_restart.sh` тоже обновлён на этот путь
- В `process_manager.py` добавлена функция `_read_stderr()` — теперь при падении Claude CLI реальная ошибка видна в логах и отправляется пользователю
- 385 юнит/интеграционных тестов, 6 E2E тестов — все PASS
- Rate limit utilization ~96% — близко к лимиту, может влиять на ответы Claude CLI
