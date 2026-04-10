# Аудит незавершённых задач по Telegram-боту Claude Manager

**Дата аудита:** 10-04-2026, 20:26
**Проект:** Claude Manager (Telegram-бот — пульт управления Claude Code с телефона)
**Статус:** Список открытых задач на момент аудита
**Источники:** сессионные отчёты за 09-04 и 10-04, текущий `git status` / `git diff HEAD`, живые логи бота `~/Library/Logs/claude-manager.error.log`

## Резюме

Самая важная находка — **в свежих логах после перезапуска бота в 20:05:39 спам «Нет timestamp в файле сессии» полностью пропал**, хотя до перезапуска в файле было 38 222 таких строки. Это значит два критичных фикса (`session_reader.py` и `claude_runner.py`) уже применены в рабочей копии и уже работают в живом процессе (PID 89196), но **ни один из них не закоммичен** — в `git status` они стоят как `M` после коммита `5b0ac7e` (project-switching).

Всё остальное — это либо висящие наблюдения из root-cause отчётов, которые откладывались отдельными тикетами, либо опциональные следующие итерации фичи переключения проектов.

## Группировка по критичности

### 🔴 Критично: висят незакоммиченные фиксы в production-коде

#### 1. Фикс `session_reader.py` — поиск первой строки с timestamp

**Источник:** сессионный отчёт [19-56_diagnose-bot-silent-permission-mode.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-56_diagnose-bot-silent-permission-mode.md)

Claude CLI версии 2.1.96 начал писать первой строкой JSONL-файла сессии служебное событие `permission-mode` без поля `timestamp`. Старый код в `_read_session_file` брал жёстко `parsed_lines[0]` → возвращал `None` → все свежие сессии выпадали из `get_recent_sessions`, ломая watcher, команды `/sessions`, дневные номера, переключение активной сессии. Прямой путь Telegram → Claude через stdin/stdout не пострадал, но live-мониторинг встал.

В рабочей копии фикс уже лежит — обход всех строк до первой с `timestamp`, а не жёсткое взятие `parsed_lines[0]`. Но:

- фикс не в коммите (`M src/claude_manager/session_reader.py`)
- регрессионный тест с фикстурой «первая строка permission-mode» не добавлен в `tests/test_session_reader.py`
- регрессия не зафиксирована в [dev/docs/claude-cli-stream-json-protocol.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/claude-cli-stream-json-protocol.md) раздела «известные баги»

#### 2. Фикс `claude_runner.py` — буфер 16 MB и таймаут 300 секунд

**Источник:** сессионный отчёт [19-23_fix-limitoverrunerror-claude-runner.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-23_fix-limitoverrunerror-claude-runner.md)

В diff `claude_runner.py` видны два отдельных фикса:

- **`STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024`** — передаётся через параметр `limit=` в `asyncio.create_subprocess_exec()`. Закрывает `asyncio.LimitOverrunError` на длинных стоковых строках от Claude (дефолт `StreamReader` 64 KB не покрывает markdown-ответы и tool_result от `Read`/`Bash` для больших файлов)
- **`READ_LINE_TIMEOUT_SECONDS = 300`** с `asyncio.wait_for` вокруг `readline()` — защита от «зависшего» Claude CLI. Этого фикса в сессионном отчёте 19-23 не упоминалось, но в diff присутствует — либо сделан в рамках той же работы, либо в параллельной сессии без отдельного отчёта

Оба фикса с тестами регрессии в `tests/test_claude_runner.py`, но **ничего не в коммите** (`M src/claude_manager/claude_runner.py`, `M tests/test_claude_runner.py`).

#### 3. Эмпирическая верификация фикса `LimitOverrunError`

**Источник:** секция «Незавершённое» отчёта [19-23_fix-limitoverrunerror-claude-runner.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-23_fix-limitoverrunerror-claude-runner.md)

Ни e2e-прогон, ни ручной тест с реальным длинным ответом Claude не делался. Простая проверка: попросить бота «распечатай содержимое большого файла» и убедиться что в `claude-manager.error.log` нет нового `LimitOverrunError`.

#### Почему это пункт №1 на сейчас

PID 89196 (бот запущен в 20:05) уже импортировал файлы с фиксами — поэтому в логе после 20:05:39 warning «Нет timestamp» пропали. Но если сейчас сделать `git stash` или перезапуск бота в чистом чекауте (например, на другой машине или после `git reset --hard`), регрессия вернётся мгновенно. Работа без коммита — это работа без защитной сетки.

### 🔴 Критично: старый error.log распух до 8.7 MB

#### 4. Ротация `error.log` через `RotatingFileHandler`

**Источники:** [18-12_apply-root-cause-fixes-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/18-12_apply-root-cause-fixes-path-encoding.md) (рекомендация R8), [16-10_rca-session-reader-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/16-10_rca-session-reader-path-encoding.md)

Файл `~/Library/Logs/claude-manager.error.log` сейчас 8.7 MB. В нём 38 222 повтора «Нет timestamp» — это хвост ДО перезапуска 20:05. После перезапуска в логе чисто, но защиты от нового всплеска нет. Рекомендация R8 в пайплайне 18-12 включала ротацию как опциональную часть и отложила её в отдельный коммит, который так и не был сделан. Патч: `RotatingFileHandler` в `src/claude_manager/main.py:_setup_logging`.

Прецедент уже был — в отчёте 16-10 упомянуто, что до фикса `_encode_project_path` лог дорастал до **314 МБ**. Без ротации этот риск сохраняется.

### 🟡 Среднее: висящие наблюдения из root-cause, не закрытые отдельными тикетами

#### 5. Баг `_acquire_lock` в `main.py:50`

**Источники:** [16-10_rca-session-reader-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/16-10_rca-session-reader-path-encoding.md), [18-12_apply-root-cause-fixes-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/18-12_apply-root-cause-fixes-path-encoding.md)

Lock-файл `~/.claude-manager.lock` открывается в режиме `"w"` — это **труккирует** (обнуляет) PID предыдущего процесса **до** вызова `fcntl.flock()`. Если две копии бота гонятся — вторая вайпнет PID первой, и первая больше не сможет понять, кто держит lock. Фикс на 4 строки: открыть в `"r+"`, после успешного `flock` сделать `truncate(0)` + `write`.

#### 6. Потеря текста при гонке параллельных сообщений

**Источники:** [16-10_rca-session-reader-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/16-10_rca-session-reader-path-encoding.md), [18-12_apply-root-cause-fixes-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/18-12_apply-root-cause-fixes-path-encoding.md)

В `bot.py:396-404` при `ProcessManagerError` не сохраняется текст второго сообщения пользователя в очередь — оно просто теряется. За 11 дней 14 случаев в логе. Пользователь работает над сообщением, оно уходит в дыру, ответа нет, повторного шанса нет. Нужен отдельный root-cause или спека — это не однострочный фикс, это вопрос очереди сообщений.

#### 7. Координация `watch_and_restart.sh` с LaunchAgent

**Источник:** [16-10_rca-session-reader-path-encoding.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/16-10_rca-session-reader-path-encoding.md) (вторичное наблюдение)

Скрипт `watch_and_restart.sh` не знает про LaunchAgent `com.ivan.claude-manager`. При ручном запуске через watch может параллельно работать копия от LaunchAgent — обе попытаются слушать Telegram. Lock-файл их разведёт, но только когда обе стартанут почти одновременно (и при этом риск пункта 5 выстрелит). Нужна явная проверка LaunchAgent'а перед стартом.

### 🟡 Среднее: документация не догоняет код

#### 8. Зафиксировать регрессию permission-mode в протоколе stream-json

**Источник:** [19-56_diagnose-bot-silent-permission-mode.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-56_diagnose-bot-silent-permission-mode.md)

В `dev/docs/claude-cli-stream-json-protocol.md` есть раздел «известные баги». Туда добавить: «начиная с Claude CLI 2.1.96 первой строкой JSONL сессии идёт служебное событие `permission-mode` без `timestamp`; любой код, привязанный к `parsed_lines[0]`, сломается».

#### 9. Документация про `LimitOverrunError`

**Источник:** [19-23_fix-limitoverrunerror-claude-runner.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-23_fix-limitoverrunerror-claude-runner.md)

Туда же, в раздел «известные баги»: дефолт `StreamReader` 64 KB не покрывает длинные tool_result'ы и markdown-ответы Claude, нужен `limit=16MB` в `asyncio.create_subprocess_exec()`.

### 🟢 Опциональные улучшения фичи «переключение проектов»

Все пять пунктов — из секции «Что можно сделать в следующей итерации» отчёта [19-51_feature-pipeline-project-switching.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-51_feature-pipeline-project-switching.md):

#### 10. E2E тест для CJM-11

Сценарий с Telethon: отправка `/projects`, проверка списка проектов, клик на `/pN`, проверка подтверждения переключения. Пайплайн фазу 7 пропустил — E2E не запускался автоматически.

#### 11. Показ статистики проекта в списке `/projects`

Количество сессий, дата последней активности рядом с именем проекта. Сейчас только имя + маркер ● для текущего.

#### 12. Inline keyboard с callback_data

Альтернатива текстовым командам `/pN`. Даёт более богатый UX, но требует переработки механизма. Сейчас использован подход единообразия с `/N` для сессий.

#### 13. Индивидуальное переключение по `chat_id`

Для multi-tenant сценариев. Сейчас переключение глобальное на весь бот (локальный use case на 1-2 пользователя — индивидуальное было бы перебором).

#### 14. Разбить `switch_project` (~65 строк) на подфункции

По стандарту `CLAUDE.md` функции должны быть до 20 строк. `switch_project` оставлена как единый блок ради читаемости (линейная последовательность с множеством return-веток ошибок), но это info-уровневое нарушение стандарта.

### 🔵 Мета-задача

#### 15. Сохранить правило `feedback_agent_limits_behavior` в долгосрочную память

**Источник:** [19-51_feature-pipeline-project-switching.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/session-reports/10-04/19-51_feature-pipeline-project-switching.md), секция «Ретроспектива процесса»

Это не про код бота, но упомянуто как открытое. Правило: «если `Agent tool` отказал по лимиту токенов — НЕ переключайся автоматически в режим `делаю сам`, сообщи пользователю о лимите и альтернативах (ждать до сброса, попробовать одиночные вызовы вместо параллельных, отложить задачу), решение принимает пользователь».

Связано с root-cause отчётом [10-04_20-21_feature-pipeline-orchestrator-self-execution.md](file:///Users/ivan/Desktop/claude-sandbox/claude_manager/dev/docs/logs/root-cause-reports/10-04_20-21_feature-pipeline-orchestrator-self-execution.md) — это более широкий архитектурный анализ той же проблемы.

## Рекомендованный приоритет действий

1. **Закоммитить фиксы прямо сейчас** — отдельным коммитом `session_reader.py` + тест permission-mode, отдельным — `claude_runner.py` + тесты. Это закрывает пункты 1 и 2 и ставит защитную сетку из тестов регрессии
2. **Ручная проверка `LimitOverrunError`** (пункт 3) — 5 минут работы, закрывает гипотетический риск «фикс не работает на реальных данных»
3. **Ротация `error.log`** (пункт 4) — 20 строк кода в `main.py`, гарантирует что файл не вернётся к 300 MB как было раньше
4. **Баг `_acquire_lock`** (пункт 5) — 4 строки, закрывает класс «race между двумя стартами бота»
5. **Остальное по мере сил** — пункты 6-9 дозировать, пункты 10-14 — опциональные следующие итерации, пункт 15 — записать в память проекта при первом удобном случае

## Контекст для следующей сессии

**Текущее состояние процесса бота:**

- PID 89196, запущен в 20:05 через LaunchAgent `com.ivan.claude-manager`
- Импортировал код `session_reader.py` и `claude_runner.py` уже с фиксами — работает корректно
- В свежих логах после 20:05:39 warning «Нет timestamp» не наблюдаются
- Мониторит 17 сессий, привязки восстановлены, сессии #4-#8 зарегистрированы после перезапуска

**Что лежит в рабочей копии без коммита** (по `git diff HEAD` на 20:26):

- `src/claude_manager/session_reader.py` — фикс permission-mode
- `src/claude_manager/claude_runner.py` — фикс `LimitOverrunError` + таймаут 300 сек
- `tests/test_session_reader.py` — новые тесты (45 добавлений)
- `tests/test_claude_runner.py` — новые тесты (27 добавлений)
- `tests/e2e/test_session_flow.py` — изменения (84 добавления)
- `tests/integration/test_concurrent_access.py`, `test_message_path.py`, `test_session_lifecycle.py` — мелкие правки
- `.claude/skills/AGENTS.md` — перезапись тонкого варианта (из отчёта 19-19)
- `dev/docs/session-reports/09-04/19-41_finish-dev-paths-migration.md` — дополнение
- `dev/docs/logs/root-cause-fixes/.../orchestrator-log.json` — лог пайплайна path-encoding

**Последние коммиты в истории:**

- `5b0ac7e feat(project-switching)` — фича `/projects` и `/pN`
- `cf663a5 test(e2e)` — новый CJM-10 про watcher
- `afaaf45 fix(session_reader)` — фикс алгоритма `_encode_project_path` (sanitizePath)
- `9363552 refactor(skills)` — синхронизация 13 скиллов с эталонными шаблонами
- `647e40f refactor` — миграция `development/` → `dev/` и применение эталонного стандарта

**Где смотреть детали по каждому пункту:** ссылки на сессионные отчёты приведены внутри каждой секции в формате markdown `file://` — в Claude Code они кликабельны.
