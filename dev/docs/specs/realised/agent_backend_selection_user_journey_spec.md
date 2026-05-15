# Спецификация пользовательского сценария: agent_backend_selection_user_journey

Дата: 07-05-2026

Сценарий: переключение CLI-бэкенда через команду `/agent`

Целевое место в BRD: новый **CJM-16: Переключение CLI-бэкенда (`/agent`)** в `dev/docs/brd/brd-user-journeys.md`.

## Назначение

Команда `/agent` позволяет пользователю выбрать, какой CLI будет использоваться для новых сессий: Claude Code CLI или Codex CLI. Выбор глобальный для всего процесса бота и сохраняется в `~/.claude-manager-current-backend`.

Сценарий не переносит существующие сессии между CLI. Уже созданная сессия всегда продолжает работать через backend, который её создал.

## Пользовательская модель

- **Текущий backend** — глобальный выбор для будущих новых сессий.
- **Backend сессии** — свойство конкретной сессии, сохранённое в `daily_session_registry` и `session_manager`.
- **Переключение `/agent`** меняет только текущий backend.
- **Команда `/new` после `/agent`** создаёт новую сессию уже через выбранный backend.
- **Команда `/N`** подключает к сессии и использует backend этой сессии, а не глобальный текущий backend.

Пример:

1. Пользователь работает в сессии `#1 🤖 Claude`.
2. Отправляет `/agent` и выбирает `⚡ Codex`.
3. Сессия `#1` остаётся Claude-сессией.
4. Пользователь отправляет `/new`.
5. Новая сессия `#2` создаётся как `⚡ Codex`.

## Что делает пользователь

1. Отправляет `/agent`.
2. Видит список доступных CLI-бэкендов.
3. Нажимает кнопку нужного backend-а.
4. Получает подтверждение переключения.
5. При необходимости отправляет `/new`, чтобы начать новую сессию на выбранном backend-е.

## Что видит пользователь

При текущем backend `🤖 Claude`:

```text
Текущий агент: 🤖 Claude
```

Inline-кнопки:

- `✓ 🤖 Claude`
- `⚡ Codex`

После выбора Codex:

```text
Теперь новые сессии будут создаваться через ⚡ Codex.
Текущая сессия #1 остаётся на 🤖 Claude.
Чтобы начать новую Codex-сессию, отправьте /new.
```

Если активной сессии нет:

```text
Теперь новые сессии будут создаваться через ⚡ Codex.
Чтобы начать новую сессию, отправьте /new.
```

Если пользователь нажал уже выбранный backend:

```text
Уже выбран: 🤖 Claude.
```

## Что происходит внутри

### Открытие `/agent`

1. `bot.handle_agent` проверяет доступ через `_check_access`.
2. Читает текущий backend: `current_backend_registry.get_current()`.
3. Получает список всех backend-ов: `get_all_backends()`.
4. Строит inline-клавиатуру из `backend.display_name`.
5. У текущего backend-а добавляет префикс `✓`.
6. Отправляет сообщение с `reply_markup=InlineKeyboardMarkup(...)`.

Callback data:

- `agent:claude`
- `agent:codex`

Строка callback data должна использовать `BackendName.value`, а не display name.

### Выбор backend-а

1. `bot.handle_agent_callback` проверяет доступ пользователя из callback query.
2. Парсит callback data: `BackendName(raw_value)`.
3. Сравнивает target backend с `current_backend_registry.get_current()`.
4. Если target уже выбран — отвечает «Уже выбран» и не пишет файл.
5. Если target отличается — вызывает `current_backend_registry.set_current(target_backend)`.
6. После успешной записи формирует подтверждение.
7. Активная сессия читается через `session_manager.get_active_session(chat_id)` только для пояснения пользователю; она не меняется.

### Влияние на `/new`

`/new` после переключения использует:

```python
backend = current_backend_registry.get_current()
await session_manager.create_new_session(chat_id, backend)
```

Это единственный основной пользовательский путь, который напрямую зависит от глобального выбора `/agent`.

### Влияние на `/N`

`/N` не читает `current_backend_registry`. Backend берётся из `DailySessionEntry`, найденного по номеру:

```python
entry = await daily_session_registry.lookup_by_number(day_number)
await session_manager.set_active_session(chat_id, entry.session_id, entry.backend)
```

Если пользователь выбрал Codex через `/agent`, но нажал `/1`, где `/1` — Claude-сессия, сообщения продолжат идти в Claude.

### Влияние на `/stop`

`/stop` не читает `current_backend_registry`. Backend берётся из активной сессии:

```python
active = session_manager.get_active_session(chat_id)
await process_manager.stop_process(active.session_id, active.backend)
```

Это нужно, чтобы Codex получал SIGINT-стратегию, а Claude — SIGTERM-стратегию.

### Влияние на watcher

Watcher работает по всем backend-ам независимо от текущего выбора `/agent`. Переключение на Codex не отключает мониторинг Claude-сессий и наоборот.

## Публичные тексты

### `/agent`

Основное сообщение:

```text
Текущий агент: {display_name}
```

Где `display_name`:

- `🤖 Claude`
- `⚡ Codex`

### Успешное переключение

С активной сессией:

```text
Теперь новые сессии будут создаваться через {new_display_name}.
Текущая сессия #{number} остаётся на {active_display_name}.
Чтобы начать новую {new_backend_name}-сессию, отправьте /new.
```

Без активной сессии:

```text
Теперь новые сессии будут создаваться через {new_display_name}.
Чтобы начать новую сессию, отправьте /new.
```

### Уже выбранный backend

```text
Уже выбран: {display_name}.
```

### Ошибка переключения

```text
Не удалось переключить агента: {reason}
```

Причина берётся из исключения `current_backend_registry.set_current`, но длинные traceback-и пользователю не показываются. Полный traceback уходит в лог.

## Обработка ошибок

- **Неавторизованный пользователь отправил `/agent`** — запрос игнорируется, как остальные команды. В лог пишется warning.
- **Callback data повреждена** — бот отвечает `Неизвестный агент` и логирует warning с сырым callback data.
- **`current_backend_registry.load_state()` не смог прочитать файл на старте** — `set_current` выбрасывает `RuntimeError`; пользователь видит `Не удалось переключить агента: текущий backend не загружен с диска, перезапустите бота`.
- **Ошибка записи на диск** — `set_current` пробрасывает `OSError`; пользователь видит `Не удалось переключить агента: <текст ошибки ОС>`. In-memory backend не меняется.
- **Backend binary не установлен** — `/agent` всё равно позволяет выбрать backend. Ошибка появится при первом `/new` + сообщении, когда `process_manager` попытается запустить CLI. Причина: `/agent` управляет настройкой, а не проверяет окружение каждый раз.
- **Пользователь нажал старую inline-кнопку после изменения списка backend-ов** — unknown value обрабатывается как повреждённая callback data.

## Состояния пользователя

`/agent` не добавляет новое состояние пользователя. Существующие состояния остаются:

- **Нет активной сессии / режим мониторинга** — `/agent` меняет backend для будущей `/new`, но писать сообщения всё равно нельзя до выбора или создания сессии.
- **Подключён к сессии** — `/agent` меняет backend для будущей `/new`, текущая сессия не меняется.
- **Режим `/all`** — `/agent` меняет backend для будущей `/new`, watcher продолжает показывать сессии обоих backend-ов.

## Зависимости

- **`bot.py`** — реализует команду `/agent`, callback handler, inline-клавиатуру и пользовательские сообщения.
- **`current_backend_registry.py`** — читает и сохраняет глобальный выбранный backend.
- **`coding_agent_backend.py`** — даёт `BackendName`, `get_backend`, `get_all_backends`, `display_name`.
- **`session_manager.py`** — нужен только для чтения активной сессии при формировании пояснения; active session не меняется.
- **`daily_session_registry.py`** — нужен для получения дневного номера активной сессии в пояснении, если активная сессия есть и уже зарегистрирована.

## Что не входит в сценарий

- Перенос текущей сессии из Claude в Codex или обратно.
- Автоматическое создание новой сессии сразу после выбора backend-а.
- Отдельный backend на каждый проект. Выбор глобальный для всего процесса бота.
- Проверка, установлен ли бинарник выбранного CLI, при открытии `/agent`.
- Управление моделью внутри Claude или Codex. `/agent` выбирает CLI-бэкенд, а не модель.

## Тест-план

### Юнит-тесты `tests/test_bot.py`

- **test_handle_agent_shows_current_backend** — `/agent` показывает текущий backend из `current_backend_registry.get_current()`.
- **test_handle_agent_keyboard_marks_current_backend** — текущий backend получает префикс `✓`, остальные показываются без префикса.
- **test_handle_agent_keyboard_uses_backend_values_in_callback_data** — callback data равна `agent:claude` и `agent:codex`.
- **test_handle_agent_callback_switches_to_codex** — callback `agent:codex` вызывает `current_backend_registry.set_current(BackendName.CODEX)`.
- **test_handle_agent_callback_does_not_switch_when_already_current** — повторный выбор текущего backend-а не вызывает `set_current`.
- **test_handle_agent_callback_preserves_active_session** — активная сессия до и после callback остаётся той же.
- **test_handle_agent_callback_message_mentions_active_session_backend** — если активная сессия Claude, а пользователь выбрал Codex, подтверждение говорит, что текущая сессия остаётся на Claude.
- **test_handle_agent_callback_without_active_session_omits_current_session_line** — в режиме `/all` подтверждение не упоминает текущую сессию.
- **test_handle_agent_callback_handles_registry_runtime_error** — `RuntimeError` из `set_current` даёт пользователю понятное сообщение и не меняет backend.
- **test_handle_agent_callback_handles_oserror** — `OSError` из `set_current` показывается как ошибка переключения.
- **test_handle_agent_callback_rejects_unknown_backend_value** — `agent:gemini` даёт `Неизвестный агент`.
- **test_unauthorized_agent_command_ignored** — неавторизованный пользователь не получает клавиатуру.

### Интеграционные тесты

- **test_agent_switch_then_new_creates_session_on_selected_backend** — `/agent` → Codex → `/new` создаёт `ActiveSession(..., BackendName.CODEX)`.
- **test_agent_switch_does_not_change_existing_active_session_backend** — активная Claude-сессия остаётся Claude после выбора Codex.
- **test_agent_switch_persists_after_restart_load_state** — выбор Codex записан в `~/.claude-manager-current-backend`, после `load_state()` `get_current()` возвращает Codex.
- **test_existing_session_number_uses_own_backend_after_agent_switch** — после `/agent` → Codex команда `/1` для Claude-сессии создаёт `ActiveSession(..., BackendName.CLAUDE)`.

## Критерии готовности

- `/agent` есть в меню команд Telegram.
- `/agent` показывает все backend-ы из `get_all_backends()`.
- Выбранный backend сохраняется через `current_backend_registry.set_current`.
- Ошибка записи не меняет in-memory backend и видна пользователю.
- `/new` использует текущий backend после переключения.
- `/N`, `/stop`, watcher и pending delivery не используют текущий backend вместо backend-а сессии.
- В пользовательских сообщениях ясно видно, какой backend выбран и какой backend остаётся у активной сессии.
