# Протокол stream-json для Claude Code CLI

**Дата:** 30-03-2026
**Статус:** Собрано из официальных SDK, GitHub issues и сторонних реализаций
**Важно:** Официальная документация Anthropic по этому протоколу минимальна (issue #24594 закрыт как NOT_PLANNED). Этот документ — единственный полный справочник для проекта.

## Общие принципы

Claude Code CLI поддерживает программный режим работы через флаги:
- `--input-format stream-json` — принимает JSON-сообщения на stdin
- `--output-format stream-json` — выдаёт JSON-события на stdout
- `-p` — print-режим (неинтерактивный, программный)

Обмен данными — **NDJSON** (Newline Delimited JSON): один JSON-объект на строку, разделитель `\n`.

---

## Входящие сообщения (stdin → Claude CLI)

### Пользовательское текстовое сообщение

```json
{"type": "user", "message": {"role": "user", "content": "Текст сообщения"}}
```

**Поля:**
- **type** — всегда `"user"`
- **message.role** — всегда `"user"`
- **message.content** — строка (простой текст) или массив content blocks

**КРИТИЧЕСКИ ВАЖНО:** Старый формат `{"type": "user_message", "content": "..."}` — НЕВАЛИДНЫЙ. Claude CLI молча игнорирует его и зависает в ожидании правильного сообщения. Никакой ошибки не выдаётся.

### Пользовательское сообщение с изображением

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      {"type": "text", "text": "Описание"},
      {
        "type": "image",
        "source": {
          "type": "base64",
          "media_type": "image/png",
          "data": "base64_данные_изображения"
        }
      }
    ]
  }
}
```

### Необязательные поля

- **session_id** — идентификатор сессии (используется в Agent SDK)
- **parent_tool_use_id** — `null` для обычных сообщений, ID tool_use для ответов субагентов

---

## Исходящие события (Claude CLI → stdout)

### system — инициализация сессии

Первое событие после запуска процесса. Содержит метаданные сессии.

```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "uuid-сессии",
  "tools": ["Bash", "Read", "Edit", "Write", "..."],
  "model": "claude-opus-4-6[1m]",
  "claude_code_version": "2.1.81",
  "permissionMode": "default"
}
```

### assistant — ответ Claude

Содержит текст ответа, вызовы инструментов или блоки размышлений.

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "Ответ Claude"},
      {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "ls"}},
      {"type": "thinking", "thinking": "Размышления Claude", "signature": "..."}
    ]
  },
  "session_id": "uuid-сессии"
}
```

**Content blocks бывают:**
- `text` — текстовый блок ответа
- `tool_use` — вызов инструмента (Bash, Read, Edit и др.)
- `thinking` — блок размышлений (extended thinking)

### user (tool_result) — результат выполнения инструмента

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      {
        "tool_use_id": "tool-1",
        "type": "tool_result",
        "content": "результат выполнения инструмента"
      }
    ]
  },
  "session_id": "uuid-сессии"
}
```

### result — финальный результат запроса

Последнее событие для текущего запроса. После него Claude ждёт следующее сообщение.

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "result": "Финальный текстовый ответ",
  "duration_ms": 5000,
  "duration_api_ms": 4500,
  "num_turns": 3,
  "session_id": "uuid-сессии",
  "total_cost_usd": 0.00123,
  "usage": {
    "input_tokens": 150,
    "output_tokens": 75,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

**Важные поля:**
- **subtype** — `"success"` или `"error_during_execution"`
- **is_error** — `true` если Claude вернул ошибку
- **result** — финальный текст (может быть пустым — известный баг #8126)
- **session_id** — может измениться по сравнению с начальным! Нужно обновлять

### rate_limit_event — информация о лимитах

```json
{
  "type": "rate_limit_event",
  "rate_limit_info": {
    "status": "allowed_warning",
    "resetsAt": 1774839600,
    "rateLimitType": "five_hour",
    "utilization": 0.9
  },
  "session_id": "uuid-сессии"
}
```

### system (api_retry) — автоматический ретрай API

```json
{
  "type": "system",
  "subtype": "api_retry",
  "attempt": 1,
  "max_retries": 5,
  "retry_delay_ms": 1000,
  "error_status": 429,
  "error": "rate_limit"
}
```

---

## Control Protocol (управляющие сообщения)

Доступен при `--input-format stream-json`. Позволяет управлять процессом Claude CLI поверх того же stdin/stdout канала.

### Запрос (stdin → Claude CLI)

```json
{
  "type": "control_request",
  "request_id": "req_1_abc123",
  "request": {"subtype": "initialize", "hooks": {}}
}
```

### Ответ (Claude CLI → stdout)

```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "req_1_abc123",
    "response": {"supported_commands": ["..."]}
  }
}
```

### Доступные команды (subtypes)

- **initialize** — регистрация хуков, MCP серверов, агентов
- **interrupt** — прервать текущую задачу
- **set_model** — сменить модель
- **set_permission_mode** — сменить режим пермиссий
- **stop_task** — остановить фоновую задачу
- **mcp_status** / **mcp_toggle** / **mcp_reconnect** — управление MCP серверами
- **get_account_info** / **get_models** / **get_commands** — получение информации

### Запрос разрешения на инструмент (Claude CLI → stdout)

Claude запрашивает разрешение перед выполнением инструмента:

```json
{
  "type": "control_request",
  "request_id": "req_3_abc",
  "request": {
    "subtype": "can_use_tool",
    "tool_name": "Bash",
    "input": {"command": "ls -la"}
  }
}
```

Ответ (stdin → Claude CLI):

```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "req_3_abc",
    "response": {"allowed": true}
  }
}
```

**Примечание:** При `--dangerously-skip-permissions` запросы разрешений не приходят.

---

## Синтетические сообщения в JSONL-файлах сессий

JSONL-файлы сессий (`~/.claude/projects/{project}/*.jsonl`) могут содержать ассистентские сообщения, которые **не были сгенерированы моделью**. Это синтетические placeholder-сообщения, вставляемые самим CLI для поддержания API-валидности истории диалога. При диагностике «пустых ответов» их нужно отличать от реальных ответов модели.

### `"No response requested."` — resume-placeholder

Когда CLI запускается с флагом `--resume` и последнее сообщение в истории — от пользователя (ассистент не успел ответить: процесс упал, был прерван, таймаут), модуль восстановления диалога вставляет фейковый ассистентский ответ со строкой `"No response requested."`. Без этого Anthropic API отклонит запрос: последовательность сообщений должна чередоваться user/assistant, а «висящее» пользовательское сообщение без пары ломает контракт.

- Вставка: `conversationRecovery.ts:231-244`
- Константа `NO_RESPONSE_REQUESTED` входит в набор `SYNTHETIC_MESSAGES`: `utils/messages.ts:302-307`

### Фильтрация из stream-json

Синтетические сообщения **удаляются** при формировании поля `result` в финальном событии `result` (см. выше раздел про `result`). Потребитель stream-json (бот, SDK-клиент) получает пустую строку `""`, а не текст `"No response requested."`.

- Фильтр: `QueryEngine.ts:1124-1131` — при сборке result event тексты из `SYNTHETIC_MESSAGES` исключаются из финального `result`

То есть синтетические сообщения видны **только** при прямом чтении JSONL-файла сессии. Через stream-json их нет.

### Полный список синтетических сообщений

В набор `SYNTHETIC_MESSAGES` (`utils/messages.ts:302-307`) входят:

- **`NO_RESPONSE_REQUESTED`** (`"No response requested."`) — resume-placeholder (описан выше)
- **`INTERRUPT_MESSAGE`** — вставка при прерывании текущего turn пользователем (Ctrl+C, `interrupt` через control protocol)
- **`CANCEL_MESSAGE`** — вставка при отмене операции
- **`REJECT_MESSAGE`** — вставка при отклонении tool-use (запрет выполнения инструмента)

Все четыре константы фильтруются из stream-json одинаково (`QueryEngine.ts:1124-1131`).

### Второй контекст `NO_RESPONSE_REQUESTED` — тихий fallback модели при rate limit

Та же константа `NO_RESPONSE_REQUESTED` используется ещё в одном месте — при автоматическом понижении модели (Opus → Sonnet) после срабатывания rate limit. Код вставляет синтетический ассистентский ответ, чтобы переключить модель без видимого разрыва диалога.

- Вставка: `errors.ts:529-534`

Результат для читающего JSONL: в момент fallback-а в файле может появиться запись `"No response requested."`, привязанная не к resume-операции, а к смене модели внутри одной активной сессии.

### Диагностическое правило

Появление `"No response requested."` в JSONL-файле сессии **не является** признаком того, что CLI вернул пустой ответ. Это resume-артефакт (или fallback-артефакт при смене модели). Для диагностики проблемы «пустой ответ CLI» (issue #8126) эту строку использовать **нельзя** — её нужно явно отфильтровать при анализе истории диалога по файлу сессии.

Для детекции реальных пустых ответов модели смотреть на событие `result` в stream-json (поле `result` равно `""` или отсутствует) — туда синтетические сообщения не попадают.

---

## Известные баги и ограничения

- **Пустой result** (issue #8126) — поле `result` в финальном ResultMessage иногда приходит пустым (~40% случаев в некоторых конфигурациях)
- **Зависание на втором сообщении** (issue #3187) — процесс может зависнуть при отправке второго сообщения через stdin
- **Дублирование записей** (issue #5034) — записи в JSONL-файле сессии могут дублироваться при stream-json input
- **Буферизация stdout** (issue #25670) — stdout может не flush-иться при piped output (буфер ~4-8KB)
- **Невалидный формат — молчаливое зависание** — если отправить невалидный JSON или неправильный формат сообщения, CLI молча ждёт правильное сообщение без какой-либо ошибки
- **Первая строка JSONL без поля `timestamp`** (регрессия с Claude CLI 2.1.96) — раньше первой строкой файла сессии (`.jsonl` в `~/.claude/projects/{project}/`) было пользовательское сообщение или событие `session_started`, у которых `timestamp` есть всегда. Начиная с 2.1.96 первой строкой идёт служебное событие (`permission-mode`, иногда `file-history-snapshot`) вообще без поля `timestamp`. Любой код, который жёстко берёт `parsed_lines[0]` для определения времени создания сессии, получит `None` и потеряет сессию. В Claude Manager это проявилось в функции `_read_session_file` (читает JSONL-файл сессии и достаёт метаданные) — watcher (фоновый цикл опроса сессий) переставал видеть свежие сессии, команда `/sessions` отдавала старый список, дневные номера не присваивались. Решение — итерировать строки до первой с полем `timestamp`, а не полагаться на индекс. В Claude Manager это реализовано в `src/claude_manager/session_reader.py:_read_session_file`.
- **Дефолтный буфер `StreamReader` 64 KB мал для реальных событий** — функция `asyncio.create_subprocess_exec()` (запуск дочернего процесса в asyncio) по умолчанию использует `StreamReader` (класс чтения потокового вывода подпроцесса) с лимитом строки 64 KB. Это слишком мало для stream-json от Claude CLI: один длинный markdown-ответ или результат `Read`/`Bash` для большого файла спокойно превышает лимит. Метод `readline()` падает с `asyncio.LimitOverrunError` (ошибка переполнения буфера в asyncio), в коде это выглядит как внезапный обрыв процесса Claude без явной причины — срабатывает ретрай, пользователь видит странные пропуски в ответах. Решение — явно передавать `limit=16 * 1024 * 1024` (16 MB) при вызове `asyncio.create_subprocess_exec()`, этого с большим запасом хватает на любые реальные события. В Claude Manager это сделано в `src/claude_manager/claude_runner.py` через константу `STREAM_BUFFER_LIMIT_BYTES`.

---

## Ошибки транспорта (stdin/stdout pipe)

При записи в stdin или чтении из stdout процесса Claude CLI могут возникнуть исключения, связанные с разрывом pipe — канала связи между родительским процессом (ботом) и дочерним (Claude CLI).

Все они наследуются от `ConnectionError`:

- **`BrokenPipeError`** — запись в pipe, когда читающая сторона (Claude CLI) уже закрыла свой конец. Возникает синхронно при вызове `stdin.write()`.
- **`ConnectionResetError`** — процесс на другом конце pipe умер или закрыл соединение. Возникает асинхронно при вызове `stdin.drain()` (финализация записи в asyncio transport).
- **`ConnectionAbortedError`** — соединение прервано локальной стороной. Теоретически возможно для pipe, на практике редко.
- **`ConnectionRefusedError`** — применимо только к сокетам, не к pipe. Упомянуто для полноты иерархии `ConnectionError`.

**Рекомендация:** ловить базовый `ConnectionError` вместо перечисления подклассов — покрывает все варианты и защищает от регрессий при появлении новых подтипов ошибок.

**Пример:**

```python
try:
    self.process.stdin.write(data)
    await self.process.stdin.drain()
except ConnectionError as pipe_error:
    raise ClaudeProcessError(
        f"Не удалось записать в stdin: {type(pipe_error).__name__}"
    ) from pipe_error
```

---

## Источники

- [Headless mode documentation](https://code.claude.com/docs/en/headless) — официальная документация по `-p` режиму
- [CLI reference](https://code.claude.com/docs/en/cli-reference) — полный список CLI флагов
- [Agent SDK streaming output](https://platform.claude.com/docs/en/agent-sdk/streaming-output) — типы StreamEvent
- [Agent SDK streaming vs single mode](https://platform.claude.com/docs/en/agent-sdk/streaming-vs-single-mode) — формат входных сообщений
- [Agent SDK Python reference](https://platform.claude.com/docs/en/agent-sdk/python) — все типы сообщений
- [GitHub issue #24594](https://github.com/anthropics/claude-code/issues/24594) — обсуждение отсутствия документации + reverse-engineered формат
- [Go SDK subprocess package](https://pkg.go.dev/github.com/dotcommander/agent-sdk-go/claude/subprocess) — полная спецификация control protocol
