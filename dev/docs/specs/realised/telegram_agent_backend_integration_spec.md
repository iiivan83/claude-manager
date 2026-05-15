# Спецификация интеграции: telegram_agent_backend_integration

Дата: 07-05-2026

Слой: 4–5 (orchestration + Telegram transport)

Файлы:
- `src/claude_manager/bot.py`
- `src/claude_manager/claude_interaction.py`
- `src/claude_manager/claude_runner.py`
- `src/claude_manager/main.py`

## Назначение

Эта спецификация закрывает пробел между backend-aware модулями и пользовательским Telegram-слоем. Нижние спеки уже описывают `CodingAgentBackend`, Claude/Codex implementations, `current_backend_registry`, backend-aware `daily_session_registry`, `session_manager`, `process_manager`, `session_watcher` и `unread_buffer`. Здесь фиксируется, как `bot.py`, `claude_interaction.py` и `claude_runner.py` должны связать эти контракты в один пользовательский поток.

Главный инвариант: Telegram-facing код никогда не выбирает CLI по одному `session_id`. Для существующей сессии он берёт backend из `ActiveSession` или `DailySessionEntry`; для новой сессии — из `current_backend_registry.get_current()`.

## Проблема

Без отдельной integration spec остаются незафиксированными самые рискованные потребительские места:

- `/agent` может переключить глобальный backend, но `/new`, `/N`, `/stop` и обычное сообщение продолжат вызывать Claude-only API.
- `session_id_callback` может обновить `session_manager`, но потерять backend при temp→real remap.
- UI может продолжить писать «Claude» в ошибках, ретраях, busy-сообщениях и `/stop`, даже когда работает Codex.
- Watcher callback может получить сообщение от Codex, но `bot.py` проверит текущую сессию только по `session_id` и ошибочно оформит чужую backend-сессию как текущую.
- `claude_runner.py` может остаться владельцем Claude-specific JSON-протокола, хотя парсинг и кодирование уже должны принадлежать backend-адаптерам.

## Связанные документы

- `dev/docs/specs/module-dependency-graph.md` — порядок реализации и слойность.
- `dev/docs/specs/agent_backend_selection_user_journey_spec.md` — пользовательский сценарий `/agent`.
- `dev/docs/specs/coding_agent_backend_spec.md` — общий backend contract.
- `dev/docs/specs/claude_code_backend_spec.md` — Claude implementation.
- `dev/docs/specs/codex_backend_spec.md` — Codex implementation.
- `dev/docs/specs/current_backend_registry_spec.md` — глобальный выбранный backend.
- `dev/docs/specs/daily_session_registry_spec.md` — дневные номера с `DailySessionEntry`.
- `dev/docs/specs/session_manager_spec.md` — активная сессия как `ActiveSession`.
- `dev/docs/specs/process_manager_spec.md` — lifecycle процессов по `(session_id, backend)`.
- `dev/docs/specs/session_watcher_spec.md` — watcher callback с backend.
- `dev/docs/specs/unread_buffer_spec.md` — pending state по `(session_id, backend)`.

## Обслуживаемые сценарии

- **CJM-02: Отправка текстового сообщения** — `bot.handle_message` передаёт текст в `claude_interaction.send_to_claude_and_respond`. `claude_interaction` читает `ActiveSession(session_id, backend)` через `session_manager.get_active_session(chat_id)` и вызывает `process_manager.send_message(..., backend=active.backend, cwd=original_project_path)`.
- **CJM-03: Отправка фотографии или файла** — файл по-прежнему сохраняется на диск, путь включается в `prompt_text`. Backend выбирается из активной сессии; `image_paths` можно передать как отдельный список для будущего перехода Codex на `-i`, но текущий контракт допускает пустой список.
- **CJM-04: `/new`** — `bot.handle_new` берёт `backend = current_backend_registry.get_current()` и вызывает `session_manager.create_new_session(chat_id, backend)`. Подтверждение пользователю показывает дневной номер и backend display name.
- **CJM-05: `/sessions`** — `bot.handle_sessions` собирает последние сессии из всех backend-ов через `get_all_backends()`, регистрирует каждую пару `(session_id, backend)` в `daily_session_registry`, отправляет plain text список с кликабельными `/N`.
- **CJM-06: `/N`** — `session_manager.switch_to_session` возвращает `SwitchResult` с backend. Подтверждение пользователю показывает backend найденной сессии. Последующие сообщения идут именно в этот backend, независимо от текущего глобального выбора `/agent`.
- **CJM-07/CJM-10: watcher** — watcher callback передаёт `backend`. `bot.send_watcher_message` считает сообщение текущим только если совпали и `session_id`, и `backend`.
- **CJM-08: `/stop`** — `bot.handle_stop` читает `ActiveSession`, проверяет `process_manager.has_process(session_id, backend)` и `process_manager.is_busy(session_id, backend)`, затем вызывает `process_manager.stop_process(session_id, backend)`.
- **CJM-11: переключение проектов** — pending messages несут backend. При доставке `bot` регистрирует сессию через `daily_session_registry.register_session(pending.session_id, pending.backend)` и форматирует заголовок с backend display name.
- **CJM-16: `/agent`** — `bot.handle_agent` и callback-обработчик работают только с `current_backend_registry`; активная сессия не меняется.

## Изменения в `bot.py`

### Импорты

Добавить зависимости:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from claude_manager import current_backend_registry
from claude_manager.coding_agent_backend import BackendName, get_all_backends, get_backend
```

### Команды меню

В `BOT_COMMANDS` добавить:

- `("agent", "Выбор CLI-агента")`

Описание `/stop` должно стать backend-neutral:

- было: `"Остановить Claude"`
- стало: `"Остановить активного агента"`

### Форматирование заголовков

`_format_session_header` должен принимать backend:

```python
def _format_session_header(session_number: int, backend: BackendName, is_final: bool) -> str:
    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = get_backend(backend).display_name
    return f"#{session_number} {backend_label} {status_icon} "
```

Для чужой сессии watcher использует кликабельный номер:

```python
def _format_clickable_session_header(
    session_number: int,
    backend: BackendName,
    is_final: bool,
) -> str:
    status_icon = "\u2705" if is_final else "\u23f3"
    backend_label = get_backend(backend).display_name
    return f"<b>/{session_number}</b> {backend_label} {status_icon} "
```

Причина: один номер теперь может относиться к Claude или Codex, и пользователю нужно видеть, какой CLI дал ответ.

### `send_response`

Целевая сигнатура:

```python
async def send_response(
    chat_id: int,
    text: str,
    session_number: int,
    backend: BackendName,
    is_final: bool,
    reply_markup=None,
) -> None:
    ...
```

Поведение:

1. Пустой ответ или backend-specific empty marker преобразуется в человекочитаемый текст через `claude_interaction.EMPTY_RESPONSE_TEXT`.
2. Silence mode подавляет промежуточные сообщения независимо от backend.
3. File markers обрабатываются только для финальных ответов, как сейчас.
4. Заголовок формируется через `_format_session_header(session_number, backend, is_final)`.
5. `reply_markup` прикрепляется только к последней части ответа, как сейчас.

### `send_watcher_message`

Целевая сигнатура:

```python
async def send_watcher_message(
    chat_id: int,
    text: str,
    session_id: str,
    backend: BackendName,
    session_number: int,
    is_final: bool,
) -> None:
    ...
```

Проверка текущей сессии:

```python
active = session_manager.get_active_session(chat_id)
is_current = (
    active is not None
    and active.session_id == session_id
    and active.backend == backend
)
```

Сравнение только по `session_id` запрещено.

### Watcher callback

Целевая сигнатура callback-а:

```python
async def _watcher_callback(
    chat_id: int,
    session_id: str,
    backend: BackendName,
    day_number: int,
    text: str,
    is_current: bool,
    is_final: bool,
) -> None:
    ...
```

`is_current` можно использовать как уже вычисленный флаг, но `send_watcher_message` всё равно должен быть backend-aware, чтобы не появился второй путь форматирования без backend.

### `handle_new`

Алгоритм:

1. Проверить доступ через `_check_access`.
2. Прочитать `backend = current_backend_registry.get_current()`.
3. Вызвать `result = await session_manager.create_new_session(chat_id, backend)`.
4. Получить `display_name = get_backend(result.backend).display_name`.
5. Отправить: `Создана новая сессия #N (display_name)`.

При ошибке текст должен быть backend-neutral:

- `Не удалось создать сессию. Попробуйте ещё раз`

### `handle_sessions`

Алгоритм:

1. Получить все backend instances через `get_all_backends()`.
2. Для каждого backend вызвать `await backend.list_session_files_for_project(config.WORKING_DIR)`.
3. Объединить результаты, сохраняя backend рядом с каждой `SessionFileInfo`.
4. Отсортировать общий список по `last_modified_at` убыванию.
5. Ограничить итог до 15 свежих сессий после объединения, а не по 15 на backend.
6. Для каждой записи вызвать `daily_session_registry.register_session(session.session_id, backend.name)`.
7. Отправить plain text строки:

```text
/3 ⚡ Codex Исправь падающий тест test_bot.py
/2 🤖 Claude Посмотри файл main.py
```

`parse_mode=None` обязателен, чтобы `/3` и `/2` оставались кликабельными командами Telegram.

Ошибки отдельного backend-а не должны ломать весь список:

- если Claude прочитался, а Codex упал — показать Claude-сессии и добавить строку `Codex: не удалось прочитать список сессий`;
- если оба backend-а не вернули сессии — показать `Нет сессий`.

### `handle_stop`

Алгоритм:

1. Получить `active = session_manager.get_active_session(chat_id)`.
2. Если `active is None` — ответить: `Команда /stop работает только внутри сессии. Подключитесь к сессии через /sessions`.
3. Проверить `process_manager.has_process(active.session_id, active.backend)` и `process_manager.is_busy(active.session_id, active.backend)`.
4. Если процесс не найден и не busy — ответить: `{display_name} сейчас не работает, нечего останавливать`.
5. Вызвать `result = await process_manager.stop_process(active.session_id, active.backend)`.
6. Ответить: `{display_name} остановлен`.

Нельзя вызывать `stop_process(session_id)` без backend.

### `handle_switch_session`

Алгоритм:

1. Вызвать `result = await session_manager.switch_to_session(chat_id, day_number)`.
2. Если `not result.found` — ответить `Сессия #N не найдена`.
3. Получить `display_name = get_backend(result.backend).display_name`.
4. Ответить: `Подключён к сессии #N (display_name): preview`.

Глобальный `current_backend_registry.get_current()` здесь не используется.

### `handle_agent`

Поведение `/agent` описано отдельно в `agent_backend_selection_user_journey_spec.md`. В этой integration spec фиксируются только точки подключения:

- добавить `CommandHandler("agent", handle_agent)`;
- добавить `CallbackQueryHandler(handle_agent_callback, pattern=r"^agent:(claude|codex)$")`;
- callback должен вызывать `current_backend_registry.set_current(target_backend)`;
- активная сессия не меняется.

## Изменения в `claude_interaction.py`

### Историческое имя модуля

Файл `claude_interaction.py` остаётся на месте в первой backend-aware версии, чтобы не смешивать функциональную миграцию с большим переименованием импортов. Внутренние сообщения и новые функции должны использовать термин `agent` или `backend`, а не новый текст «Claude», когда речь идёт об обоих CLI.

### Watchdog key

`watchdog_tasks` должен ключеваться парой:

```python
WatchdogKey = tuple[str, BackendName]
watchdog_tasks: dict[WatchdogKey, asyncio.Task] = {}
```

Все функции watchdog принимают `session_id` и `backend`:

- `agent_silence_watchdog(session_id, backend)`
- `start_agent_silence_watchdog(session_id, backend)`
- `cancel_agent_silence_watchdog(session_id, backend)`
- `reset_watchdog_on_progress(session_id, backend)`

Причина: одинаковый `session_id` под разными backend-ами не должен отменять чужой watchdog.

### `build_busy_message_if_busy`

Алгоритм:

1. Получить `active = session_manager.get_active_session(chat_id)`.
2. Если `active is None` — вернуть `None`.
3. Проверить `process_manager.is_busy(active.session_id, active.backend)`.
4. Если busy — вернуть `{display_name} ещё обрабатывает предыдущее сообщение. Подождите или /stop`.

### `ensure_process_running`

Функция удаляется из основного flow. Backend-aware `process_manager.send_message` сам создаёт новый subprocess на каждый turn. В `claude_interaction.send_to_claude_and_respond` не должно быть отдельного вызова `create_process`.

### `send_to_claude_and_respond`

Целевой алгоритм:

1. Захватить `original_project_path = config.WORKING_DIR`.
2. Получить `active = session_manager.get_active_session(chat_id)`.
3. Если `active is None` — отправить `MONITORING_MODE_MESSAGE` и завершить.
4. Локально сохранить `session_id = active.session_id` и `backend = active.backend`.
5. Поставить watcher на паузу: `session_watcher.pause_session(session_id, backend)`.
6. Запустить watchdog: `start_agent_silence_watchdog(session_id, backend)`.
7. Создать callback-и:
   - `_on_progress(session_id, backend, progress_text)` или замыкание, где backend захвачен локально;
   - `_on_retry(session_id, attempt, max_attempts, error_reason)`, backend захвачен локально;
   - `_on_session_id_changed(old_id, new_id, callback_backend)`.
8. Вызвать `process_manager.send_message(session_id, text, backend=backend, cwd=original_project_path, progress_callback=..., retry_callback=..., session_id_callback=...)`.
9. Если проект не сменился — обработать `SendResult`.
10. В `finally` отменить watchdog и вызвать `session_watcher.resume_session(session_id, backend)` только если проект не сменился.

Важно: `backend` не перечитывается из `current_backend_registry` внутри запроса. Если пользователь отправил `/agent` во время выполнения задачи, текущий turn продолжает работать на захваченном backend-е.

### `session_id_callback`

Целевая сигнатура callback-а из `process_manager`:

```python
async def _on_session_id_changed(
    old_id: str,
    new_id: str,
    callback_backend: BackendName,
) -> None:
    ...
```

Алгоритм:

1. Проверить, что `callback_backend == backend`, захваченный на старте. Если нет — залогировать `error` и не менять state.
2. Если проект сменился:
   - перенести watchdog с `(old_id, backend)` на `(new_id, backend)`;
   - обновить локальный `session_id = new_id`;
   - не трогать state-модули нового проекта.
3. Если проект не сменился:
   - `session_watcher.update_session_id(old_id, new_id, backend)`;
   - `await session_manager.update_session_id(chat_id, old_id, new_id)`;
   - `await daily_session_registry.update_session_id(old_id, new_id)`;
   - перенести watchdog;
   - обновить локальный `session_id = new_id`.

### `handle_claude_result`

Функция становится backend-aware. Можно оставить имя на первый этап, но поведение должно быть таким:

1. Взять `actual_session_id = result.session_id`.
2. Взять `backend = result.backend`.
3. Зарегистрировать `day_number = await daily_session_registry.register_session(actual_session_id, backend)`.
4. Если `result.is_error`:
   - `error_text = result.error_text or result.text or f"Неизвестная ошибка {display_name}"`;
   - отправить `Ошибка {display_name}: {error_text}`.
5. Иначе вызвать `send_response(chat_id, result.text, day_number, backend, is_final=True)`.

## Изменения в `claude_runner.py`

### Назначение после миграции

Новый backend-aware путь в `claude_runner.py` больше не владеет Claude-specific протоколом. Функция `start_subprocess_for_backend(...)` не должна:

- собирать аргументы `claude -p ...`;
- сериализовать пользовательское сообщение в Claude stream-json;
- парсить stdout как Claude stream-json;
- решать, какое событие финальное.

Эти операции принадлежат конкретному backend-адаптеру.

Legacy `ClaudeProcess` / `start_process` может временно сохранять Claude-specific parsing как compatibility debt для старых тестов и callers. Это не считается нарушением миграции, если новый Telegram/backend-aware flow использует только `start_subprocess_for_backend(...)` и backend-адаптеры.

### Новый публичный запуск

Целевая функция:

```python
async def start_subprocess_for_backend(
    backend: CodingAgentBackend,
    session_id: str,
    cwd: str,
    prompt_text: str,
    image_paths: list[str],
) -> BackendSubprocess:
    ...
```

`session_id` всегда обязателен. Для новой сессии вызывающий передаёт temp-id `_new_<12 hex>`, уже зарегистрированный вместе с backend в `session_manager` и `daily_session_registry`; `None` в этот слой не передаётся.

Алгоритм:

1. Получить `command_args = backend.compose_subprocess_command_args(session_id, cwd, prompt_text, image_paths)`.
2. Вызвать `asyncio.create_subprocess_exec` с `stdin=PIPE`, `stdout=PIPE`, `stderr=PIPE`, `limit=STREAM_BUFFER_LIMIT_BYTES`, `cwd=cwd`.
3. Вернуть обёртку над `asyncio.subprocess.Process`.
4. При `FileNotFoundError` или `OSError` выбросить `BackendSubprocessStartError` с текстом, который сохранит backend display name и исходную ошибку.

### Обёртка процесса

Минимальный контракт обёртки:

- `process: asyncio.subprocess.Process`
- `async write_stdin(payload: bytes) -> None` — записывает байты, если `payload` не пустой, затем закрывает stdin.
- `async read_stdout_line() -> bytes` — читает одну строку с таймаутом `READ_LINE_TIMEOUT_SECONDS`.
- `async read_stderr_text() -> str` — безопасно читает stderr для диагностики при падении.
- `def is_running() -> bool`
- `async wait() -> int`

Имя `BackendSubprocess` предпочтительнее нового кода. Если реализация оставляет `ClaudeProcess` временно для меньшего diff, это должно быть зафиксировано как технический долг и не должно просачиваться в новые спеки.

### Константы

- `READ_LINE_TIMEOUT_SECONDS = 1800` — сохраняется.
- `STREAM_BUFFER_LIMIT_BYTES = 16 * 1024 * 1024` — сохраняется.
- `TERMINATE_TIMEOUT_SECONDS` больше не является универсальной runner-константой для `/stop`; stop strategy берётся из backend через `get_stop_strategy()`.
- `CLAUDE_CLI_COMMAND`, `STREAM_JSON_INPUT_FORMAT`, `STREAM_JSON_OUTPUT_FORMAT`, `EVENT_TYPE_SYSTEM`, `EVENT_TYPE_RESULT` переезжают в `claude_code_backend.py`.

## Изменения в `main.py` и `post_init`

`bot.post_init` должен загрузить `current_backend_registry` до установки команд и старта watcher-а:

```python
try:
    current_backend_registry.load_state()
except Exception:
    logger.error("Ошибка при загрузке current backend — используется Claude", exc_info=True)
```

Если `load_state()` выставил внутренний `_loaded_from_disk=False`, `/agent` позже вернёт пользователю понятную ошибку через `set_current`. Старт бота не блокируется.

Watcher запускается через backend-aware фасад:

```python
asyncio.create_task(session_watcher.start(_watcher_callback, _get_current_session_async))
```

Где `_get_current_session_async(chat_id)` возвращает `ActiveSession | None`, а не `str | None`.

## Обработка ошибок

- **Активной сессии нет** — текстовые сообщения, фото и документы получают `MONITORING_MODE_MESSAGE`.
- **Backend binary не найден** — `process_manager` возвращает/поднимает backend-aware ошибку; `claude_interaction` показывает `Не удалось запустить {display_name}. Проверьте, что CLI установлен и доступен в PATH`.
- **Ошибка `current_backend_registry.set_current`** — `/agent` не меняет UI-состояние и сообщает причину. Память не должна уходить вперёд диска.
- **Один backend упал при `/sessions`** — список сессий второго backend-а всё равно показывается.
- **Проект сменился во время turn-а** — доставка в старый чат подавляется, как сейчас; backend не перечитывается.
- **`callback_backend` не совпал с захваченным backend** — лог `error`, state не обновляется. Это защита от программного нарушения контракта в `process_manager`.

## Тест-план

### Юнит-тесты `tests/test_bot.py`

- **test_handle_new_uses_current_backend** — при `current_backend_registry.get_current() == BackendName.CODEX` вызывается `session_manager.create_new_session(chat_id, BackendName.CODEX)`, сообщение содержит `⚡ Codex`.
- **test_handle_sessions_lists_both_backends_in_one_numbering** — Claude и Codex сессии объединяются, сортируются по `last_modified_at`, регистрируются через `register_session(session_id, backend)`, отправляются plain text строками `/N display preview`.
- **test_handle_sessions_partial_backend_failure_keeps_other_backend** — падение Codex listing не скрывает Claude-сессии.
- **test_handle_switch_session_uses_backend_from_switch_result** — подтверждение `/N` содержит backend из `SwitchResult`.
- **test_handle_stop_passes_backend_to_process_manager** — `stop_process(session_id, backend)` вызывается с backend активной сессии.
- **test_send_watcher_message_current_requires_session_and_backend_match** — одинаковый `session_id` с другим backend не считается текущей сессией.
- **test_send_response_header_contains_backend_display_name** — финальный и промежуточный заголовки содержат display name.
- **test_handle_agent_command_shows_backend_keyboard** — `/agent` строит inline-клавиатуру из `get_all_backends()`.
- **test_handle_agent_callback_switches_backend_atomically** — callback вызывает `set_current` и не меняет активную сессию.

### Юнит-тесты `tests/test_claude_interaction.py`

- **test_send_to_agent_uses_active_session_backend** — `process_manager.send_message` получает backend из `ActiveSession`, не из `current_backend_registry`.
- **test_send_to_agent_captures_backend_across_registry_change** — во время turn-а глобальный backend меняется, но callback-и и результат остаются на исходном backend.
- **test_busy_message_uses_backend_display_name** — busy-текст говорит `{display_name} ещё обрабатывает...`.
- **test_session_id_callback_preserves_backend** — temp→real remap вызывает `session_watcher.update_session_id(old, new, backend)` и сохраняет backend.
- **test_watchdog_tasks_are_keyed_by_session_and_backend** — одинаковый `session_id` под Claude и Codex имеет два независимых watchdog task.
- **test_project_switch_suppresses_delivery_but_keeps_backend_cleanup** — при смене проекта доставка подавляется, cleanup вызывает `resume_session(session_id, backend)` только для исходного проекта.

### Юнит-тесты `tests/test_claude_runner.py`

- **test_start_subprocess_uses_backend_composed_args** — runner вызывает `backend.compose_subprocess_command_args(...)`, а не собирает `claude -p` сам.
- **test_start_subprocess_sets_cwd_and_stream_buffer_limit** — `asyncio.create_subprocess_exec` получает `cwd` и `limit=STREAM_BUFFER_LIMIT_BYTES`.
- **test_write_stdin_closes_pipe_after_payload** — stdin закрывается после записи, включая пустой payload для Codex.
- **test_start_subprocess_for_backend_does_not_parse_stdout_json** — новый backend-aware runner path не парсит события; парсинг покрывается backend-тестами. Legacy `ClaudeProcess` / `start_process` может оставаться отдельным compatibility path.

### Интеграционные тесты

- **test_existing_claude_session_still_uses_claude_after_agent_codex** — создать Claude-сессию, переключить `/agent` на Codex, отправить сообщение в старую сессию; `process_manager` получает `BackendName.CLAUDE`.
- **test_new_session_after_agent_codex_uses_codex** — переключить `/agent` на Codex, выполнить `/new`, первое сообщение уходит в Codex.
- **test_stop_uses_active_session_backend** — активная Codex-сессия останавливается через Codex stop strategy.
- **test_watcher_delivery_keeps_backend_in_header** — watcher callback для Codex доставляет сообщение с `⚡ Codex` в заголовке.
- **test_project_switch_pending_delivery_registers_backend** — pending Codex-сообщение при возврате в проект регистрируется как `(session_id, BackendName.CODEX)`.

## Критерии готовности

- Все публичные вызовы `process_manager` из Telegram-facing слоя передают backend явно.
- Все вызовы `daily_session_registry.register_session` передают backend.
- В `bot.py` нет пользовательских текстов «Claude» для backend-neutral ситуаций, кроме display name конкретного backend-а.
- `/agent` влияет только на новые сессии.
- `/N` и `/stop` используют backend сессии, а не текущий глобальный backend.
- Watcher и pending delivery форматируют сообщения с backend display name.
- Новый backend-aware runner path (`start_subprocess_for_backend`) не содержит Claude-specific protocol parsing; временный legacy `ClaudeProcess` / `start_process` допустим только как явно зафиксированный compatibility debt.
