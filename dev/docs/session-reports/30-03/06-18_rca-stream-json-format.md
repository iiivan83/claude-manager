# Сессия 30-03: RCA зависания бота + документация stream-json протокола

## Резюме

Найдена и верифицирована экспериментально корневая причина зависания бота при отправке сообщений в сессию: неправильный формат JSON-сообщения в `claude_runner.py`. Исправление уже было в рабочем дереве — закоммичено. Создана полная документация протокола stream-json для Claude Code CLI.

## Изменённые файлы

- `src/claude_manager/claude_runner.py` — изменён — формат сообщения `user_message` → `user`, путь к CLI через `shutil.which`, импорт `config` модулем
- `src/claude_manager/process_manager.py` — изменён — `--resume` для временных ID `_new_XXXX` передаёт `None`
- `src/claude_manager/main.py` — изменён — `_run_bot()` стал синхронным, `_restore_state()` перенесена в `post_init`
- `src/claude_manager/bot.py` — изменён — `is_final` пробрасывается в watcher-сообщения, уведомление при сбое реестра, восстановление в `post_init`
- `src/claude_manager/session_watcher.py` — изменён — определение `is_final` по типу последнего события, `ACTIVE_EVENT_TYPES`
- `src/claude_manager/daily_session_registry.py` — изменён — `_loaded_from_disk` защита, 10 ретраев чтения файла
- `CLAUDE.md` — изменён — ссылка на протокол stream-json, предупреждение о формате `user_message`, описание ограничения `--resume`, секция защиты персистентных данных, команды LaunchAgent, ссылка на `claude-cli-stream-json-protocol.md` в «Техническая документация»
- `development/docs/deployment-guide.md` — изменён — секция автозапуска через LaunchAgents
- `development/docs/docs-index.md` — изменён — секции для `claude-cli-stream-json-protocol.md`, `root-cause-reports/`, `testing/`
- `development/docs/claude-cli-stream-json-protocol.md` — создан — полный справочник протокола stream-json (форматы stdin/stdout, control protocol, известные баги)
- `development/docs/root-cause-reports/30-03_05-08_message-not-reaching-terminal-session.md` — создан — отчёт RCA по зависанию бота
- `pipeline-state.json` — изменён — обновлено состояние фаз
- Тестовые файлы (10 шт.) — изменены — адаптация под новые сигнатуры (`is_final` в watcher, `_loaded_from_disk` в registry и др.)

## Коммиты

- `c8f7556` — fix(core): исправлен формат stream-json, защита реестра, watcher is_final
- `1821d2f` — docs: добавлена документация протокола stream-json для Claude Code CLI
- `2f907f8` — docs: обновлена документация по реальному коду — CLAUDE.md и docs-index

## Выполненные команды

- `claude -p --output-format stream-json --input-format stream-json --resume <UUID>` с новым форматом сообщения — тест прошёл за 9 сек, ответ получен
- `claude -p --output-format stream-json --input-format stream-json --resume <UUID>` со старым форматом `user_message` — **зависание навсегда, 0 байт stdout** — подтверждена корневая причина
- `python -m pytest tests/ --collect-only -q` — 384 теста, сбор успешен

## Решения

- **Решение**: Корневая причина зависания — невалидный формат JSON `{"type": "user_message", "content": text}`. Claude CLI его не понимает и молча ждёт правильное сообщение. **Причина**: Экспериментально доказано тремя тестами: новый формат работает, старый зависает, `--resume` на активную сессию не конфликтует.
- **Решение**: Создать собственную документацию протокола stream-json в проекте. **Причина**: Официальная документация Anthropic по `--input-format stream-json` практически отсутствует (issue #24594 закрыт NOT_PLANNED). Собрали из Agent SDK, GitHub issues, Go/Elixir SDK.
- **Решение**: Таймаут на `readline()` пока не добавлять — `--resume` работает корректно. **Причина**: Тесты показали что `--resume` на активную терминальную сессию не зависает. Проблема была исключительно в формате сообщения.

## Проблемы и решения

- **Проблема**: Пользователь отправил "Да" через бот после `/11` — бот завис, сообщение не появилось в терминале. **Решение**: Найдена корневая причина — старый формат JSON `user_message` вместо `user`. Исправление уже было в рабочем дереве (незакоммичено), закоммичено в этой сессии.
- **Проблема**: Первоначальная гипотеза RCA была неверной (предполагалось зависание `--resume` на активную сессию). **Решение**: Экспериментальная проверка через запуск `claude --resume` на активную сессию опровергла гипотезу. Тест со старым форматом подтвердил реальную причину.
- **Проблема**: `timeout` недоступен на macOS. **Решение**: Использовал `perl -e 'alarm 15; ...'` для таймаутов в тестах.

## Контекст для следующей сессии

- Формат stream-json исправлен и закоммичен — бот работает с правильным форматом `{"type": "user", "message": {"role": "user", "content": text}}`
- Документация протокола создана в `development/docs/claude-cli-stream-json-protocol.md`
- RCA отчёт в `development/docs/root-cause-reports/30-03_05-08_message-not-reaching-terminal-session.md` содержит чек-лист рекомендаций, часть из которых ещё не применена (таймаут на readline как страховка, проверка готовности процесса, обновление BRD CJM-06, обновление скиллов review-code и spec-module)
- Известные баги протокола (из документации): пустой result (#8126), зависание на втором сообщении (#3187), дублирование записей (#5034)
- Тесты: 384 штуки, все собираются успешно
