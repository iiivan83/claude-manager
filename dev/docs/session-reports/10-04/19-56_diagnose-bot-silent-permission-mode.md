# Сессия 10-04: диагностика «бот не присылает сообщения» — регрессия из-за Claude CLI 2.1.96

## Резюме

Диагностика без правки кода. Цель — понять почему бот перестал присылать сообщения в Telegram. Найдена первопричина: обновление Claude Code CLI до версии 2.1.96 изменило формат JSONL-файлов сессий (первая строка теперь `permission-mode` без `timestamp`), что ломает `session_reader._read_session_file` и каскадно — `session_watcher`, `session_manager`, команды `bot.py`. Фикс предложен, но не применён — ждёт решения пользователя.

## Состояние бота на момент диагностики

- Процесс: PID 67456, запущен через LaunchAgent `com.ivan.claude-manager`, работает 5 мин 54 сек на момент проверки
- Lock-файл `~/.claude-manager.lock` занят этим же процессом (fcntl.flock)
- Сетевые соединения: два активных TCP к Telegram API (`149.154.166.110:https`) — polling идёт
- stdout-лог `~/Library/Logs/claude-manager.log` — 0 байт (нормально: `logging.basicConfig()` пишет в stderr)
- stderr-лог `~/Library/Logs/claude-manager.error.log` — 5.2 МБ за 6 минут (спам WARNING'ов)

## Первопричина

Claude Code CLI версии 2.1.96 (видно в поле `version` самого JSONL) теперь пишет первой строкой файла сессии служебное событие без `timestamp`:

```json
{"type":"permission-mode","permissionMode":"default","sessionId":"910c..."}
{"parentUuid":null,...,"timestamp":"2026-04-10T14:12:55.468Z","sessionId":"910c...","version":"2.1.96",...}
{"type":"file-history-snapshot",...}
```

Раньше первой шла строка с `timestamp`. Код `session_reader.py:174-185` жёстко привязан к `parsed_lines[0]`:

```python
first_line = parsed_lines[0]
...
timestamp = first_line.get("timestamp")
if not timestamp:
    logger.warning("Нет timestamp в файле сессии: %s", file_path)
    return None
```

Теперь `first_line` — это `permission-mode`, `timestamp` отсутствует → `_read_session_file` возвращает `None` → файл сессии полностью отбрасывается из результатов `get_recent_sessions`.

## Каскадное влияние на модули

- `session_watcher.py:111` — функция выбора активной сессии для чата получает пустой список → watcher не отслеживает новые сессии → пользователь не видит обновлений (**прямая причина симптома**)
- `bot.py:310` и `bot.py:592` — обработчики команд списка сессий (например `/recent`)
- `session_manager.py:81` и `session_manager.py:146` — выбор и переключение активной сессии
- В логах побочный эффект: `Task was destroyed but it is pending!` — async-задачи watcher/process_manager не успевают завершиться

## Доказательства из логов

- 5241611 байт `claude-manager.error.log` за ~6 минут
- Спам каждые 2 секунды (интервал polling watcher): `[WARNING] claude_manager.session_reader: Нет timestamp в файле сессии: ...`
- 110 JSONL-файлов в `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude-manager/`, большинство свежих сессий пострадали

## Выполненные команды

- `ps aux | grep claude_manager` — проверка запущенных процессов бота
- `lsof ~/.claude-manager.lock` — кто держит lock-файл
- `launchctl list | grep claude` — статус LaunchAgent
- `cat ~/Library/LaunchAgents/com.ivan.claude-manager.plist` — чтение plist для поиска путей логов
- `lsof -p 67456 -a -i` — активные сетевые соединения бота
- `lsof -p 67456` — все открытые файловые дескрипторы
- `tail -50 ~/Library/Logs/claude-manager.error.log` — чтение хвоста лога
- `grep -E "ERROR|CRITICAL|Exception|Traceback" ~/Library/Logs/claude-manager.error.log` — поиск фатальных ошибок
- `head -3 /Users/ivan/.claude/projects/.../910c4775-....jsonl` — чтение первых строк проблемного JSONL
- `ls -lt .../*.jsonl | head -5` — пять самых свежих файлов сессий
- `git log --oneline -10 src/claude_manager/session_reader.py` — история изменений модуля

## Прочитанные файлы (без правок)

| Файл | Зачем читали |
|------|-------------|
| `src/claude_manager/main.py` | Понять настройку логирования — почему `.log` пустой, а `.error.log` полный |
| `src/claude_manager/session_reader.py` | Найти место привязки к первой строке JSONL и проверить логику timestamp |

## Решения

- **Решение**: отказаться от позиционной привязки к `parsed_lines[0]` при поиске timestamp. Искать первую строку, где `timestamp` есть, пропуская служебные события. **Причина**: контракт «первая строка = строка с timestamp» нигде не закреплён в исходниках Claude CLI и регулярно ломается при обновлениях. Устойчивая реализация должна искать нужное поле, а не брать фиксированную позицию.
- **Решение**: фикс **не применяется автоматически**, ждём подтверждения пользователя. **Причина**: правило «Don't add features beyond what was asked» — пользователь спросил «почему», а не «почини».

## Предложенный фикс (не применён)

Файл: `src/claude_manager/session_reader.py`, строки 174-185.

Было:
```python
first_line = parsed_lines[0]

file_basename = os.path.basename(file_path)
file_name_without_extension = file_basename.removesuffix(".jsonl")
session_id = first_line.get("sessionId", file_name_without_extension)

timestamp = first_line.get("timestamp")
if not timestamp:
    logger.warning("Нет timestamp в файле сессии: %s", file_path)
    return None
```

Стать должно:
```python
file_basename = os.path.basename(file_path)
file_name_without_extension = file_basename.removesuffix(".jsonl")
session_id = parsed_lines[0].get("sessionId", file_name_without_extension)

# Claude CLI начиная с 2.1.96 пишет служебные события (permission-mode,
# file-history-snapshot) без timestamp в начале файла — пропускаем их.
timestamp = None
for line in parsed_lines:
    if line.get("timestamp"):
        timestamp = line["timestamp"]
        break

if not timestamp:
    logger.warning("Нет ни одной строки с timestamp в файле сессии: %s", file_path)
    return None
```

## Проблемы и решения

- **Проблема**: stdout-лог `~/Library/Logs/claude-manager.log` пустой, создавалось ложное впечатление что бот не работает. **Решение**: понял что `logging.basicConfig()` по умолчанию пишет в stderr, а LaunchAgent перенаправляет stderr в отдельный файл `.error.log` — именно там все логи бота. Не баг, поведение ожидаемое.
- **Проблема**: в логе куча `Task was destroyed but it is pending!` — выглядит как независимая проблема async. **Решение**: это симптом той же первопричины — async-задачи watcher отбрасываются потому что upstream (поиск активной сессии) возвращает пустой список.

## Незавершённое

- [ ] Применить фикс в `src/claude_manager/session_reader.py:174-185` (поиск первой строки с timestamp вместо жёсткой привязки к `parsed_lines[0]`)
- [ ] Добавить тест-кейс в `tests/test_session_reader.py` с фикстурой «первая строка = permission-mode без timestamp, вторая — строка с timestamp»
- [ ] Перезапустить бот после применения фикса (через `launchctl kickstart -k gui/$(id -u)/com.ivan.claude-manager` или `watch_and_restart.sh`)
- [ ] Проверить что в `claude-manager.error.log` пропал спам WARNING'ов после рестарта
- [ ] Проверить что пользователь снова получает обновления сессий в Telegram
- [ ] Рассмотреть зафиксировать эту регрессию в `dev/docs/claude-cli-stream-json-protocol.md` — там уже есть раздел «известные баги», добавить туда наблюдение про `permission-mode` как первую строку в 2.1.96+

## Контекст для следующей сессии

**Версия Claude CLI на момент диагностики:** `2.1.96` (виден в поле `version` JSONL-файлов сессий).

**Что важно помнить:**
- `session_reader.py` — единственная точка чтения JSONL-сессий. Любой модуль, которому нужен список сессий, идёт через него → одна правка здесь чинит всё сразу
- `session_watcher.py:111` — самое чувствительное место к этому багу, потому что через него пользователь видит обновления
- `session_reader.py:182` ранее уже правили в коммите `afaaf45 fix(session_reader): воспроизведение реального алгоритма sanitizePath из Claude CLI` — это уже вторая регрессия из-за изменений в Claude CLI за короткий период. Стоит в соответствующем ADR или BRD зафиксировать: «любая привязка к формату файлов Claude CLI должна быть устойчива к добавлению новых типов служебных событий в начале файла»
- Правило из `CLAUDE.md` проекта: «Контракты с внешними системами проверяются эмпирически». Здесь оно нарушено — код полагался на наблюдаемый ранее контракт без документирования. Фикс должен восстановить устойчивость, а не просто поправить текущий случай
- В логе `claude-manager.error.log` уже 5.2+ МБ спама — после рестарта возможно имеет смысл обрезать файл (`> ~/Library/Logs/claude-manager.error.log`), чтобы в следующей диагностике легче было читать свежие события
- Бот на момент написания отчёта всё ещё запущен (PID 67456), всё ещё в сломанном состоянии — работает polling, но сессии не отслеживаются
