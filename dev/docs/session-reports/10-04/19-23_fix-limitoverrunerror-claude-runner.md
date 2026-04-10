# Сессия 10-04: фикс LimitOverrunError в claude_runner

## Резюме

Закрыт баг `asyncio.LimitOverrunError` в `claude_runner.py:113`, обнаруженный в e2e-прогоне 10-04 19:02. Дефолтный лимит `StreamReader` (64 KB) поднят до 16 MB через параметр `limit=` в `create_subprocess_exec`. Тесты зелёные (394 passed), бот перезапущен с фиксом.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `src/claude_manager/claude_runner.py` | изменён | Добавлена константа `STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024` (после `READ_LINE_TIMEOUT_SECONDS`, ~строка 30) с подробным комментарием — почему 64 KB мало и почему 16 MB безопасно. В `start_process()` (строка ~209) в вызов `asyncio.create_subprocess_exec()` добавлен параметр `limit=STREAM_BUFFER_LIMIT_BYTES` |
| `tests/test_claude_runner.py` | изменён | В импорт из `claude_manager.claude_runner` добавлена константа `STREAM_BUFFER_LIMIT_BYTES`. После `test_start_process_resume_session` добавлен новый тест `test_start_process_passes_increased_stream_buffer_limit` — проверяет что (а) `limit=` передаётся, (б) значение равно константе, (в) константа не меньше 16 MB |

## Выполненные команды

- `.venv/bin/python -m pytest tests/ -v` — полный прогон тестов после фикса. Результат: **394 passed, 0 failed, 3 warnings, 14.74s** (было 393 + новый тест регрессии)
- `.venv/bin/python -m pytest tests/test_claude_runner.py::test_start_process_passes_increased_stream_buffer_limit -v` — изолированный прогон нового теста для подтверждения. PASSED за 0.01s
- `launchctl kickstart -k gui/$UID/com.ivan.claude-manager` — перезапуск бота через LaunchAgent. Старый PID 58043 убит, новый PID **67456**, exit code 0

## Решения

- **Фикс через параметр `limit=` в `create_subprocess_exec`**, а не переписывание `readline()` на побайтное `read(n)`. **Причина**: `readline()` уже умеет правильно искать `\n` и аккумулировать буфер; переписывание на `read(n)` потребовало бы вручную делать поиск разделителя — три бага вместо одного исправления. `asyncio.create_subprocess_exec()` принимает `limit` как именованный параметр и прокидывает его в обе pipe-обёртки `StreamReader` (stdout и stderr).
- **Размер буфера — 16 MB**, а не 4 MB как рекомендовалось в отчёте e2e. **Причина**: 4 MB покрывает обычные ответы Claude, но `tool_result` от `Read`/`Bash` для больших файлов может занимать сотни KB - несколько MB. 16 MB — золотая середина: покрывает все реалистичные edge cases с большим запасом, при этом достаточно мал чтобы рантайм-баг с runaway memory остался заметным. Буфер `StreamReader` растёт по мере необходимости, заранее память не аллоцируется — это «потолок», а не «резерв».
- **Юнит-тест регрессии вместо интеграционного с реальным subprocess**. **Причина**: интеграционный тест требует запуска реального процесса, который пишет длинную строку в stdout, и сложен в поддержке. Юнит-тест проверяет три инварианта одновременно: параметр `limit` передан, значение синхронизировано с константой, константа не уменьшена ниже 16 MB. Этого достаточно для защиты от регрессии «кто-то случайно удалил параметр».
- **Документация `dev/docs/claude-cli-stream-json-protocol.md` не обновлялась**. **Причина**: пользователь явно не просил, действует правило «не редактировать файлы, которые пользователь не просил менять».

## Проблемы и решения

- **Проблема**: `pgrep -fl "python -m claude_manager"` не нашёл процесс, хотя `launchctl list` показывал PID 58043. **Решение**: проверил через `ps -p 58043` — процесс реально существует, просто в командной строке полный путь `/usr/local/Cellar/python@3.13/.../Python -m claude_manager`, а не короткое `python`. Использовал более широкий паттерн `pgrep -fl claude_manager`.
- **Проблема**: основной лог `~/Library/Logs/claude-manager.log` оказался пустым (size 0, дата 30 марта). **Решение**: проверил `~/Library/LaunchAgents/com.ivan.claude-manager.plist` — там два пути для логов: `StandardOutPath` и `StandardErrorPath`. Python `logging` по умолчанию пишет в stderr, поэтому реальные логи — в `~/Library/Logs/claude-manager.error.log`.
- **Проблема**: при остановке старого процесса в логе появилась строка `[ERROR] asyncio: Task was destroyed but it is pending!`. **Анализ**: это шум от asyncio при SIGTERM (висящая task не успела доделаться до сигнала), не связан с фиксом и не является регрессией. После старта нового процесса свежих ERROR/Traceback в логе нет.

## Незавершённое

- [ ] **Эмпирическая верификация фикса в реальных условиях** — не было прогона ни e2e, ни ручного теста с длинным ответом Claude. Тесты регрессии пройдены, но реального подтверждения «LimitOverrunError больше не появляется в логах при стриминге длинного `assistant.content`» не делалось. Простой ручной тест: написать боту запрос, заведомо порождающий длинный markdown-ответ (например, попросить распечатать содержимое большого файла), и убедиться что в `claude-manager.error.log` нет нового `LimitOverrunError`.
- [ ] **Документация `dev/docs/claude-cli-stream-json-protocol.md`** не обновлена в разделе «Известные баги». Можно добавить упоминание `LimitOverrunError` и сделанный фикс — но только по явной просьбе пользователя.
- [ ] **Коммит фикса** — не сделан, по правилу «не коммитить без явной просьбы». Готов к коммиту через скилл `commit`. Изменены 2 файла: `src/claude_manager/claude_runner.py`, `tests/test_claude_runner.py`.
- [ ] **Перенесённые тикеты из e2e отчёта 10-04 19:02** остаются открытыми и не относятся к этой сессии: warning «Нет timestamp в файле сессии» (1601 раз за 5 минут работы бота), ротация `error.log` через `RotatingFileHandler`.

## Контекст для следующей сессии

**Что было исправлено и где.** Класс багов «контракт с внешним CLI» по `LimitOverrunError`. Корень — в `src/claude_manager/claude_runner.py`, функция `start_process()` (~строка 209). До фикса `asyncio.create_subprocess_exec()` создавал `StreamReader` для stdout/stderr с дефолтным `limit=65536` (64 KB), а строки stream-json от Claude CLI с длинным `assistant.content` или `tool_result` это превышали → `readline()` падал → бот ловил исключение в `read_events()` (`claude_runner.py:113`) → process_manager запускал ретрай → пользователь получал ответ со второй попытки. Баг был скрыт ретраями и заметен только по тратам времени и засору `error.log`.

**Текущее состояние бота.** Запущен через LaunchAgent `com.ivan.claude-manager`, PID **67456** (новый, после `launchctl kickstart -k`). Конфигурация загружена, привязки сессий восстановлены, мониторинг 3 сессий запущен. Свежих ERROR в логе нет.

**Где живёт код.** Фикс — в одном месте: `src/claude_manager/claude_runner.py`. Дополнительных мест запуска subprocess для Claude CLI в проекте нет (проверено через `grep create_subprocess_exec src/`). В `process_manager.py:174-183` (`_read_stderr`) используется `process.stderr.read(500)` — это `read(n)` с явным размером, у этого метода нет проблемы `LimitOverrunError`, фикс там не нужен.

**Связанные документы.** Корневой отчёт e2e, который зафиксировал баг — `dev/docs/logs/testing/e2e-test-results_10-04-19-02.md`. Он же содержит описание двух других открытых тикетов (warning timestamp, ротация лога). Фикс контракта по `_encode_project_path` (`session_reader.py`) сделан в коммите `afaaf45` ранее этим днём — это другой класс багов «контракт с CLI», но из той же категории «контракты проверяются эмпирически, а не по догадке» (правило из CLAUDE.md).

**Тест регрессии.** Если в будущем кто-то будет рефакторить `start_process()` и случайно удалит параметр `limit=` или уменьшит значение константы ниже 16 MB — `test_start_process_passes_increased_stream_buffer_limit` упадёт. Это защита, а не «декоративный» тест.
