# Спецификация модуля: claude_runner

Дата: 30-03-2026
Слой: 1 (зависит от слоя 0)
Файл: `src/claude_manager/claude_runner.py`

## Назначение

Обёртка для запуска Claude Code CLI через subprocess. Создаёт процесс Claude с протоколом stream-json, отправляет сообщения пользователя через stdin и читает потоковые JSON-события из stdout. Модуль работает на уровне одного процесса — не знает о сессиях, нумерации и Telegram.

## Обслуживаемые сценарии

- **CJM-02: Отправка текстового сообщения** — claude_runner запускает процесс Claude (или использует существующий при resume) и передаёт текстовое сообщение через stdin. Читает потоковые JSON-события из stdout и возвращает их вызывающему коду (process_manager).
- **CJM-03: Отправка фотографии или файла** — аналогично CJM-02, но сообщение содержит путь к файлу и инструкцию для Claude прочитать его. Для claude_runner разницы нет — это просто текстовое сообщение.
- **CJM-04: Создание новой сессии (/new)** — claude_runner запускает новый процесс Claude без resume (новая сессия). Процесс создаётся сразу при /new и ждёт первого сообщения.

## Публичный API

### `async start_process(session_id: str | None = None) -> ClaudeProcess`

Запускает новый процесс Claude Code CLI. Если передан session_id — запускает с resume (продолжение существующей сессии). Если session_id равен None — запускает новую сессию.

**Аргументы:**
- `session_id` (str | None) — идентификатор сессии Claude для возобновления (формат UUID, например `"84748107-a3de-4314-8c72-4c3b1b6e3605"`). None означает новую сессию.

**Возвращает:** объект `ClaudeProcess` — обёртка над запущенным процессом с методами для отправки сообщений и чтения событий.

**Исключения:**
- `ClaudeStartError` — если процесс не удалось запустить (например, Claude Code CLI не найден в PATH, или произошла ошибка при создании subprocess)

### `class ClaudeProcess`

Обёртка над запущенным процессом Claude Code CLI. Предоставляет методы для взаимодействия с процессом через stream-json протокол.

#### `async send_message(text: str) -> None`

Отправляет текстовое сообщение в процесс Claude через stdin в формате stream-json.

**Аргументы:**
- `text` (str) — текст сообщения пользователя (например, `"Посмотри файл main.py и скажи что он делает"`)

**Возвращает:** ничего

**Исключения:**
- `ClaudeProcessError` — если процесс уже завершился или stdin недоступен

#### `async read_events() -> AsyncGenerator[dict, None]`

Асинхронный генератор, который читает JSON-события из stdout процесса Claude. Каждое событие — один JSON-объект на отдельной строке. Генератор завершается, когда приходит событие `result` (финальный ответ) или процесс завершается.

**Аргументы:** нет

**Возвращает:** асинхронный итератор словарей — каждый словарь представляет одно JSON-событие от Claude

**Исключения:**
- `ClaudeProcessError` — если произошла ошибка чтения из stdout или невалидный JSON

#### `async terminate() -> None`

Принудительно завершает процесс Claude. Сначала отправляет SIGTERM (мягкое завершение), ждёт до 5 секунд. Если процесс не завершился — отправляет SIGKILL (принудительное).

**Аргументы:** нет

**Возвращает:** ничего

#### `is_running() -> bool`

Проверяет, работает ли процесс Claude в данный момент.

**Аргументы:** нет

**Возвращает:** True если процесс активен, False если завершился

#### `session_id: str | None`

Атрибут — идентификатор сессии Claude. Заполняется из события `system` (subtype: init) при старте процесса. До получения первого события — None.

#### `process: asyncio.subprocess.Process`

Атрибут — ссылка на нижележащий объект asyncio subprocess. Предоставляет доступ к pid, returncode и другим свойствам процесса для process_manager.

### `class ClaudeStartError(Exception)`

Исключение при невозможности запустить процесс Claude. Содержит описание причины (CLI не найден, ошибка subprocess).

### `class ClaudeProcessError(Exception)`

Исключение при ошибках взаимодействия с запущенным процессом (запись в stdin, чтение из stdout, невалидный JSON).

## Внутренние функции

### `_build_command_args(session_id: str | None) -> list[str]`

Собирает список аргументов командной строки для запуска Claude Code CLI.

**Аргументы:**
- `session_id` (str | None) — идентификатор сессии для resume, или None для новой сессии

**Возвращает:** список строк — аргументы командной строки (например, `["claude", "-p", "--output-format", "stream-json", "--verbose", ...]`)

### `_parse_event(raw_line: str) -> dict | None`

Разбирает одну строку stdout как JSON-событие. Пропускает пустые строки.

**Аргументы:**
- `raw_line` (str) — одна строка из stdout процесса

**Возвращает:** словарь с данными события, или None если строка пустая

**Исключения:**
- `ClaudeProcessError` — если строка не является валидным JSON

### `_extract_session_id_from_event(event: dict) -> str | None`

Извлекает session_id из события stream-json. Session_id присутствует в большинстве событий, но гарантированно — в событии `system` (subtype: init) и `result`.

**Аргументы:**
- `event` (dict) — разобранное JSON-событие

**Возвращает:** строку с session_id, или None если в событии нет поля session_id

## Алгоритм работы

### start_process

1. **Собрать аргументы CLI** — вызвать `_build_command_args(session_id)` для формирования команды запуска
2. **Получить рабочую директорию** — импортировать `WORKING_DIR` из модуля config. Это директория, в контексте которой Claude будет работать
3. **Запустить subprocess** — вызвать `asyncio.create_subprocess_exec()` с аргументами:
   - `stdin=asyncio.subprocess.PIPE` — для отправки сообщений
   - `stdout=asyncio.subprocess.PIPE` — для чтения событий
   - `stderr=asyncio.subprocess.PIPE` — для перехвата ошибок
   - `cwd=WORKING_DIR` — рабочая директория
4. **Обернуть в ClaudeProcess** — создать объект `ClaudeProcess` с ссылкой на subprocess
5. **Залогировать запуск** — записать в лог (уровень info): PID процесса, session_id (если resume), рабочую директорию
6. **Вернуть ClaudeProcess** — объект готов к использованию

Если subprocess не удалось создать (FileNotFoundError — CLI не найден, или OSError), выбросить `ClaudeStartError` с описанием проблемы.

### _build_command_args

1. **Начать с базовой команды** — `["claude", "-p"]`. Флаг `-p` (print) запускает Claude в неинтерактивном режиме — он читает stdin, отвечает и может продолжать ждать новый ввод
2. **Добавить формат вывода** — `["--output-format", "stream-json"]`. Потоковый JSON — каждое событие (размышление, использование инструментов, ответ) отдельным JSON-объектом
3. **Добавить verbose** — `["--verbose"]`. Обязательный флаг при использовании stream-json с -p — без него CLI выдаёт ошибку
4. **Добавить формат ввода** — `["--input-format", "stream-json"]`. Позволяет отправлять сообщения в формате JSON через stdin
5. **Добавить полный доступ к инструментам** — `["--dangerously-skip-permissions"]`. Пропускает все проверки разрешений — Claude может выполнять bash-команды, редактировать файлы, читать файлы без подтверждения пользователя
6. **Добавить resume, если нужно** — если `session_id` не None, добавить `["--resume", session_id]`. Это продолжает существующую сессию Claude вместо создания новой
7. **Вернуть список аргументов** — полный список строк для `asyncio.create_subprocess_exec()`

### ClaudeProcess.send_message

1. **Проверить, что процесс жив** — если `process.returncode` не None (процесс завершился), выбросить `ClaudeProcessError("Процесс Claude уже завершился")`
2. **Проверить, что stdin доступен** — если `process.stdin` равен None, выбросить `ClaudeProcessError("stdin процесса Claude недоступен")`
3. **Сформировать JSON-сообщение** — создать словарь `{"type": "user_message", "content": text}`, сериализовать в JSON-строку через `json.dumps(message, ensure_ascii=False)` (ensure_ascii=False сохраняет кириллицу как есть, а не кодирует в escape-последовательности)
4. **Записать в stdin** — вызвать `process.stdin.write()` с JSON-строкой, завершённой символом новой строки (`\n`). Затем `await process.stdin.drain()` для принудительной отправки данных. При `BrokenPipeError` — выбросить `ClaudeProcessError` (процесс закрылся между проверкой returncode и записью)
5. **Залогировать отправку** — записать в лог (уровень debug): длину сообщения в символах

### ClaudeProcess.read_events

1. **Начать цикл чтения** — читать stdout построчно через `process.stdout.readline()`
2. **Проверить конец потока** — если readline() вернул пустые байты (`b""`), процесс завершился. Выйти из генератора
3. **Декодировать строку** — преобразовать байты в строку (UTF-8), убрать `\n` в конце
4. **Разобрать JSON** — вызвать `_parse_event(line)`. Если вернул None (пустая строка) — пропустить, перейти к следующей строке
5. **Извлечь session_id** — вызвать `_extract_session_id_from_event(event)`. Если session_id получен и self.session_id ещё не установлен — сохранить его
6. **Отдать событие** — `yield event`
7. **Проверить финальное событие** — если `event["type"] == "result"`, это последнее событие от Claude для текущего запроса. Выйти из генератора

### ClaudeProcess.terminate

1. **Проверить, что процесс ещё работает** — если `process.returncode` не None, процесс уже завершён, ничего не делать
2. **Отправить SIGTERM** — вызвать `process.terminate()`. Это мягкий сигнал завершения — процесс может корректно очистить ресурсы
3. **Подождать завершения** — `await asyncio.wait_for(process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS)`. Ждать максимум 5 секунд
4. **Если не завершился за 5 секунд** — при `asyncio.TimeoutError` вызвать `process.kill()` (SIGKILL — принудительное завершение), затем `await process.wait()` для очистки зомби-процесса
5. **Залогировать завершение** — записать в лог (уровень info): PID процесса, код завершения (returncode)

### ClaudeProcess.is_running

1. **Проверить returncode** — если `process.returncode` равен None — процесс работает, вернуть True. Иначе — вернуть False

## Формат событий stream-json

Каждая строка stdout — один JSON-объект. Типы событий, которые приходят от Claude Code CLI:

### Событие `system` (инициализация)

Приходит первым при старте процесса. Содержит session_id и информацию о среде.

```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605",
  "cwd": "/Users/ivan/project",
  "model": "claude-opus-4-6[1m]",
  "tools": ["Bash", "Read", "Edit", "Write", ...],
  "claude_code_version": "2.1.81"
}
```

### Событие `assistant` (ответ Claude)

Содержит текст ответа Claude или вызов инструмента. Может приходить несколько раз за один запрос (когда Claude использует инструменты).

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "Привет! Чем могу помочь?"}
    ]
  },
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"
}
```

Вариант с вызовом инструмента:

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "tool_use", "id": "toolu_01...", "name": "Write", "input": {"file_path": "/tmp/test.txt", "content": "hello"}}
    ]
  },
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"
}
```

### Событие `user` (результат инструмента)

Приходит после того, как Claude выполнил инструмент. Содержит результат выполнения.

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      {"tool_use_id": "toolu_01...", "type": "tool_result", "content": "File created successfully at: /tmp/test.txt"}
    ]
  },
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"
}
```

### Событие `result` (финальный результат)

Приходит последним. Содержит итоговый текст ответа, статус, session_id и метрики.

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "result": "Привет! Чем могу помочь?",
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605",
  "duration_ms": 4806,
  "num_turns": 1
}
```

Вариант с ошибкой:

```json
{
  "type": "result",
  "subtype": "error",
  "is_error": true,
  "result": "Error: ...",
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"
}
```

### Событие `rate_limit_event` (информация о лимитах)

Информационное событие о текущем состоянии лимитов API. Не содержит полезных данных для бота — можно пропускать.

```json
{
  "type": "rate_limit_event",
  "rate_limit_info": {
    "status": "allowed_warning",
    "resetsAt": 1775142000,
    "rateLimitType": "seven_day",
    "utilization": 0.75
  },
  "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"
}
```

**Важно:** claude_runner не фильтрует события — он передаёт все события через `read_events()`. Фильтрация и интерпретация типов событий — ответственность process_manager.

## Зависимости

- **config** — `WORKING_DIR` — рабочая директория для cwd при запуске subprocess
- **asyncio** (стандартная библиотека) — `create_subprocess_exec()`, `subprocess.PIPE`, `wait_for()` — создание и управление процессом
- **json** (стандартная библиотека) — `json.dumps()`, `json.loads()` — сериализация/десериализация JSON-событий
- **logging** (стандартная библиотека) — логирование запуска, завершения и ошибок процесса

## Обработка ошибок

- **Claude Code CLI не найден в PATH** — при `FileNotFoundError` от subprocess выбрасывается `ClaudeStartError("Claude Code CLI не найден. Убедитесь, что 'claude' доступен в PATH")`. Логируется на уровне error
- **Ошибка создания процесса (OSError)** — при любом `OSError` от subprocess выбрасывается `ClaudeStartError` с описанием ошибки. Логируется на уровне error
- **Процесс завершился неожиданно** — если readline() возвращает пустые байты, read_events завершает итерацию. process_manager обнаружит завершение через `is_running()` или через завершение итератора
- **Невалидный JSON в stdout** — если строка stdout не является корректным JSON, выбрасывается `ClaudeProcessError(f"Невалидный JSON от Claude: '{raw_line[:200]}'")`. Логируется на уровне error. process_manager решает, как поступить (может проигнорировать или завершить процесс)
- **Запись в stdin после завершения процесса** — `send_message()` проверяет `process.returncode` перед записью. Если процесс завершился — выбрасывается `ClaudeProcessError("Процесс Claude уже завершился")`
- **Stdin недоступен** — при `BrokenPipeError` или отсутствии stdin выбрасывается `ClaudeProcessError`. Логируется на уровне error
- **Таймаут terminate** — если процесс не завершился за 5 секунд после SIGTERM, отправляется SIGKILL. Логируется на уровне warning: "Процесс Claude не завершился по SIGTERM, отправлен SIGKILL"

## Константы

- `CLAUDE_CLI_COMMAND = "claude"` — имя исполняемого файла Claude Code CLI. Вынесено в константу, чтобы легко заменить путь при необходимости (например, при тестировании или если CLI установлен в нестандартное место)
- `TERMINATE_TIMEOUT_SECONDS = 5` — максимальное время ожидания завершения процесса после SIGTERM (в секундах). 5 секунд — достаточно для корректной очистки ресурсов, но не слишком долго для пользователя
- `STREAM_JSON_INPUT_FORMAT = "stream-json"` — формат ввода для stdin (значение аргумента --input-format)
- `STREAM_JSON_OUTPUT_FORMAT = "stream-json"` — формат вывода для stdout (значение аргумента --output-format)
- `EVENT_TYPE_SYSTEM = "system"` — тип события инициализации от Claude CLI
- `EVENT_TYPE_RESULT = "result"` — тип финального события от Claude CLI (сигнал завершения ответа)

## Тест-план

### Юнит-тесты

- **test_build_command_args_new_session** — проверяет формирование аргументов CLI для новой сессии (без resume)
  - Вход: `session_id=None`
  - Ожидаемый результат: `["claude", "-p", "--output-format", "stream-json", "--verbose", "--input-format", "stream-json", "--dangerously-skip-permissions"]`. Аргумент `--resume` отсутствует
  - Тип: unit

- **test_build_command_args_resume_session** — проверяет формирование аргументов CLI для resume существующей сессии
  - Вход: `session_id="84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Ожидаемый результат: список аргументов содержит `"--resume"` и `"84748107-a3de-4314-8c72-4c3b1b6e3605"` в конце
  - Тип: unit

- **test_parse_event_valid_json** — проверяет разбор корректной JSON-строки
  - Вход: `'{"type": "system", "subtype": "init", "session_id": "abc-123"}'`
  - Ожидаемый результат: словарь `{"type": "system", "subtype": "init", "session_id": "abc-123"}`
  - Тип: unit

- **test_parse_event_empty_line** — проверяет, что пустая строка возвращает None
  - Вход: `""`
  - Ожидаемый результат: `None`
  - Тип: unit

- **test_extract_session_id_from_system_event** — проверяет извлечение session_id из события system
  - Вход: `{"type": "system", "subtype": "init", "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605"}`
  - Ожидаемый результат: `"84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Тип: unit

- **test_extract_session_id_from_result_event** — проверяет извлечение session_id из события result
  - Вход: `{"type": "result", "subtype": "success", "session_id": "84748107-a3de-4314-8c72-4c3b1b6e3605", "result": "Привет"}`
  - Ожидаемый результат: `"84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Тип: unit

- **test_extract_session_id_missing** — проверяет, что отсутствие session_id в событии возвращает None
  - Вход: `{"type": "rate_limit_event", "rate_limit_info": {}}`
  - Ожидаемый результат: `None`
  - Тип: unit

- **test_start_process_new_session** — проверяет запуск нового процесса Claude (mock subprocess)
  - Вход: `session_id=None`, mock `asyncio.create_subprocess_exec` возвращает mock-процесс
  - Ожидаемый результат: возвращён объект `ClaudeProcess`, subprocess создан с правильными аргументами, `cwd=WORKING_DIR`
  - Тип: unit

- **test_start_process_resume_session** — проверяет запуск процесса с resume (mock subprocess)
  - Вход: `session_id="84748107-a3de-4314-8c72-4c3b1b6e3605"`, mock subprocess
  - Ожидаемый результат: аргументы subprocess содержат `"--resume"` и session_id
  - Тип: unit

- **test_send_message_writes_json_to_stdin** — проверяет, что send_message записывает корректный JSON в stdin
  - Вход: `text="Посмотри файл main.py"`, mock stdin
  - Ожидаемый результат: в stdin записан `'{"type": "user_message", "content": "Посмотри файл main.py"}\n'`
  - Тип: unit

- **test_read_events_yields_parsed_events** — проверяет, что read_events возвращает разобранные JSON-события
  - Вход: mock stdout отдаёт две строки — событие system и событие result
  - Ожидаемый результат: итератор возвращает два словаря с правильным содержимым, после result итератор завершается
  - Тип: unit

- **test_read_events_sets_session_id** — проверяет, что session_id устанавливается из первого события
  - Вход: mock stdout отдаёт событие system с session_id, затем result
  - Ожидаемый результат: `claude_process.session_id == "84748107-a3de-4314-8c72-4c3b1b6e3605"`
  - Тип: unit

- **test_terminate_sends_sigterm_then_waits** — проверяет, что terminate сначала отправляет SIGTERM
  - Вход: mock-процесс, который завершается после terminate()
  - Ожидаемый результат: вызван `process.terminate()`, затем `process.wait()`
  - Тип: unit

- **test_is_running_returns_true_when_active** — проверяет, что is_running возвращает True для работающего процесса
  - Вход: mock-процесс с `returncode=None`
  - Ожидаемый результат: `True`
  - Тип: unit

- **test_is_running_returns_false_when_finished** — проверяет, что is_running возвращает False для завершённого процесса
  - Вход: mock-процесс с `returncode=0`
  - Ожидаемый результат: `False`
  - Тип: unit

### Граничные случаи

- **test_read_events_skips_empty_lines** — проверяет, что пустые строки в stdout пропускаются
  - Вход: mock stdout отдаёт: пустая строка, событие system, пустая строка, событие result
  - Ожидаемый результат: итератор возвращает два события (system и result), пустые строки пропущены
  - Тип: edge case

- **test_read_events_stops_on_empty_bytes** — проверяет, что итератор завершается при закрытии stdout (процесс завершился)
  - Вход: mock stdout отдаёт событие system, затем `b""` (конец потока)
  - Ожидаемый результат: итератор возвращает одно событие и завершается
  - Тип: edge case

- **test_read_events_stops_on_result_event** — проверяет, что итератор завершается после события result
  - Вход: mock stdout отдаёт событие system, событие assistant, событие result, ещё одно событие system (от следующего запроса)
  - Ожидаемый результат: итератор возвращает три события (system, assistant, result) и завершается. Четвёртое событие не прочитано
  - Тип: edge case

- **test_send_message_with_unicode** — проверяет корректную отправку сообщений с Unicode-символами
  - Вход: `text="Привет 🌍 мир"`, mock stdin
  - Ожидаемый результат: JSON в stdin содержит корректный Unicode
  - Тип: edge case

- **test_send_message_with_special_json_characters** — проверяет экранирование спецсимволов JSON в тексте сообщения
  - Вход: `text='Найди строку "hello" в файле'`, mock stdin
  - Ожидаемый результат: кавычки в тексте экранированы в JSON (`\"hello\"`)
  - Тип: edge case

- **test_terminate_already_finished_process** — проверяет, что terminate на уже завершённом процессе не вызывает ошибку
  - Вход: mock-процесс с `returncode=0` (уже завершён)
  - Ожидаемый результат: метод завершается без ошибок, terminate() и kill() не вызваны
  - Тип: edge case

- **test_session_id_updated_from_later_event** — проверяет, что session_id не перезаписывается из последующих событий, если уже установлен
  - Вход: mock stdout отдаёт system с session_id="aaa", затем result с session_id="aaa"
  - Ожидаемый результат: `claude_process.session_id == "aaa"` — установлен из первого события и не менялся
  - Тип: edge case

### Тесты ошибок

- **test_start_process_cli_not_found** — проверяет ошибку при отсутствии Claude CLI в PATH
  - Вход: mock `asyncio.create_subprocess_exec` выбрасывает `FileNotFoundError`
  - Ожидаемый результат: `ClaudeStartError` с текстом, содержащим `"Claude Code CLI не найден"`
  - Тип: error

- **test_start_process_os_error** — проверяет ошибку при общей ошибке ОС
  - Вход: mock `asyncio.create_subprocess_exec` выбрасывает `OSError("Permission denied")`
  - Ожидаемый результат: `ClaudeStartError` с описанием ошибки
  - Тип: error

- **test_send_message_to_finished_process** — проверяет ошибку при отправке в завершённый процесс
  - Вход: mock-процесс с `returncode=1` (уже завершился)
  - Ожидаемый результат: `ClaudeProcessError` с текстом `"Процесс Claude уже завершился"`
  - Тип: error

- **test_send_message_stdin_none** — проверяет ошибку когда stdin недоступен
  - Вход: mock-процесс с `stdin=None`
  - Ожидаемый результат: `ClaudeProcessError` с текстом `"stdin процесса Claude недоступен"`
  - Тип: error

- **test_send_message_broken_pipe** — проверяет ошибку при разрыве pipe
  - Вход: mock stdin.write() выбрасывает `BrokenPipeError`
  - Ожидаемый результат: `ClaudeProcessError` с описанием ошибки
  - Тип: error

- **test_parse_event_invalid_json** — проверяет ошибку при невалидном JSON
  - Вход: `"это не json {{{"`
  - Ожидаемый результат: `ClaudeProcessError` с текстом, содержащим `"Невалидный JSON от Claude"` и первые символы невалидной строки
  - Тип: error

- **test_terminate_sigkill_after_timeout** — проверяет, что SIGKILL отправляется после таймаута SIGTERM
  - Вход: mock-процесс, process.wait() выбрасывает `asyncio.TimeoutError`
  - Ожидаемый результат: вызван `process.terminate()`, затем `process.kill()`, затем `process.wait()` повторно
  - Тип: error
