# Process Manager State Split Design

## Цель

Уменьшить `src/claude_manager/process_manager.py` без изменения поведения бота.
Первый разрез выносит управление состоянием процессов в отдельный модуль, а
`process_manager.py` оставляет оркестратором запуска, отправки сообщений, retry и
stop.

## Контекст

`process_manager.py` сейчас отвечает сразу за несколько разных задач:

- хранит глобальное состояние процессов CLI;
- управляет ключами Claude/Codex backend-ов;
- поддерживает alias temp session id -> real session id;
- читает stream-json события;
- запускает retry loop;
- останавливает процессы через `/stop`.

Файл вырос до 1328 строк. Это выше проектного hard-threshold для крупных файлов.
Цель первого шага — снизить размер и отделить конкурентное состояние от
жизненного цикла процесса, не меняя публичный контракт.

## Подход

Создать модуль `src/claude_manager/process_state.py`.

Новый модуль отвечает только за in-memory state:

- `_processes`;
- `_busy_flags`;
- `_busy_lock`;
- `_stop_events`;
- `_session_id_aliases`;
- `ProcessKey`;
- `ManagedProcess`;
- построение и разбор process key;
- разрешение alias-ов session id;
- удаление alias-ов;
- перенос состояния при смене session id;
- проверки `is_busy` и `has_process`.

`process_manager.py` импортирует эти объекты и функции из `process_state.py`.
Для обратной совместимости приватные имена остаются доступными как атрибуты
`process_manager`, потому что существующие тесты и несколько интеграционных
сценариев напрямую проверяют `_processes`, `_busy_flags`, `_stop_events`,
`_busy_lock` и `_session_id_aliases`.

## Границы модулей

### `process_state.py`

Единственная ответственность: хранить и атомарно обновлять состояние процессов.

Содержит:

- type aliases `ProcessKey` и `ManagedProcess`;
- глобальные словари состояния;
- `_make_process_key`;
- `_make_backend_process_key`;
- `_split_process_key`;
- `_resolve_process_key_alias_unlocked`;
- `_prefer_existing_process_key_unlocked`;
- `_resolve_session_id_alias_unlocked`;
- `_remove_session_id_aliases_unlocked`;
- `is_busy`;
- `has_process`;
- `update_session_id`.

Функции с суффиксом `_unlocked` остаются такими же по смыслу: вызывающий код
обязан держать `_busy_lock`, если операция требует атомарности.

### `process_manager.py`

Остаётся владельцем поведения:

- `create_process`;
- `send_message`;
- `_send_message_legacy_claude`;
- `_send_message_backend_aware`;
- `_execute_send`;
- `_process_events`;
- `_retry_loop`;
- `_restart_process`;
- `stop_process`;
- `stop_all_processes`.

Также модуль продолжает реэкспортировать:

- `is_busy`;
- `has_process`;
- `update_session_id`;
- приватные state-объекты, которые уже используются тестами.

## Совместимость

Публичный API не меняется.

Импорты вида:

```python
from claude_manager.process_manager import send_message, stop_process
```

должны работать без изменений.

Тестовый доступ вида:

```python
import claude_manager.process_manager as pm_module
pm_module._processes.clear()
```

тоже должен продолжить работать. Это важно, чтобы первый разрез был
поведенчески нейтральным и не превратился в массовую перепись тестов.

## Data Flow

До разреза:

1. `process_manager.py` сам хранит state.
2. `send_message`, `_restart_process`, `stop_process` и `update_session_id`
   напрямую читают и меняют словари.

После разреза:

1. `process_state.py` хранит state и helpers.
2. `process_manager.py` импортирует те же mutable-объекты.
3. Runtime-поведение не меняется, потому что импортируются сами словари и lock,
   а не копии данных.

## Testing

Первый разрез должен быть покрыт существующими тестами.

Минимальный gate:

```bash
.venv/bin/python -m pytest tests/test_process_manager.py tests/test_stop_triggers_retry_blackbox.py tests/test_stop_triggers_retry_whitebox.py tests/integration/test_cwd_pinning_across_retries.py tests/integration/test_message_path.py -q
```

После этого нужен более широкий прогон вокруг Telegram-оркестрации:

```bash
.venv/bin/python -m pytest tests/test_bot.py tests/test_claude_interaction.py tests/test_process_manager.py -q
```

Полный suite без E2E запускается перед финальным утверждением:

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q
```

E2E через Telethon не является обязательным для этого механического разреза,
потому что поведение Telegram не должно измениться. Если потребуется живой
прогон, тестовый Telegram-аккаунт используется через `E2E_TEST_USER_ID`, а не
через добавление второго человека в `ALLOWED_USER_IDS`.

## Риски

- Ошибка при переносе `_busy_lock` может сломать защиту от конкурентных
  `send_message`.
- Ошибка в alias helpers может сломать temp -> real session id remap.
- Ошибка в re-export приватных имён может сломать существующие тесты без
  runtime-регрессии.
- Если перенести слишком много поведения в первый шаг, diff станет труднее
  проверить.

## Out of Scope

- Вынос event reader из `process_manager.py`.
- Вынос retry loop.
- Изменение stop strategy.
- Изменение публичного API.
- Удаление legacy Claude-only пути.
- Переписывание тестов под новый приватный модуль.

## Expected Size Change

Ожидаемое уменьшение `process_manager.py`: примерно 250-350 строк.

После первого разреза файл всё ещё может быть выше 700 строк. Это осознанно:
вариант B должен дать безопасный крупный первый шаг. Следующий естественный
разрез — вынести event reader или retry loop отдельной задачей.
