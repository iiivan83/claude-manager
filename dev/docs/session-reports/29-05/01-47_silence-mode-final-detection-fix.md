# Сессия 29-05: фикс ложных финалов в session_watcher из-за tool_result

## Резюме

Починили баг в `session_watcher`, из-за которого в режиме тишины приходило много «финалов» с галочкой ✅ посреди работы Claude. Заменили критерий «turn активен» с проверки типа последнего record в `.jsonl`-файле сессии на reverse-скан до первого `result`-event'а — это устранило ложные финалы между шагами Claude с инструментами. Полный прогон тестов — 1020 passed.

## Изменённые файлы

- **`src/claude_manager/claude_code_session_file_reader.py`** — изменён. Добавлена функция `_compute_is_turn_active_from_parsed_records(parsed_records)`, переписана `_read_cursor_count_last_record_and_turn_active(file_path)` (раньше — `_read_cursor_record_count_and_last_record`). Оба ридера сессионных файлов (snapshot для полного парсинга, cursor для baseline в режиме `/all`) перешли на новый критерий: сканируем records с конца, ищем первый `result` или `assistant`/`progress`/`queue-operation`. Файл вырос с ~467 до 512 строк — превышение порога 500 на 12 строк зафиксировано осознанно, разбиение отложено.
- **`tests/test_claude_code_backend.py`** — изменён. Добавлены два регрессионных теста: `test_read_session_file_snapshot_turn_active_after_tool_result` и `test_read_session_file_cursor_turn_active_after_tool_result`. Оба пишут JSONL с последовательностью `[user, assistant(text+tool_use), user(tool_result)]` без `result`-event'а и проверяют `is_turn_active is True`. До фикса падали, после — зелёные.
- **`dev/docs/adr/29.05_01.47-session-change-documenter-watcher-turn-active-result-anchor.md`** — создан. ADR с описанием решения, отвергнутых альтернатив (добавить `user` в BUSY_EVENT_TYPES, лечить только silence mode, снять PAUSE_LEAK_SAFETY_TIMEOUT_SECONDS) и связи с предыдущим ADR от 28-05 по дедупликации progress+final.

## Решения

- **Решение:** Критерий «turn активен» вычисляется по позиции `result`-event'а относительно последнего `assistant` в файле, а не по типу последнего record. **Причина:** Claude CLI пишет `result` строго при штатном завершении turn'а — это надёжный маркер. Между шагами с инструментами последний record в `.jsonl` — это `user`-tool_result, и критерий «последний record не в `{assistant, progress, queue-operation}`» ошибочно считал turn закрытым, хотя Claude ещё думает над следующим шагом.
- **Решение:** Не трогать Codex-бэкенд. **Причина:** у Codex собственный надёжный критерий `is_turn_terminal_session_record` через `payload.type in {task_complete, terminal_failure...}`. Проблемы tool_result у Codex нет — там другой протокол rollout-файлов.
- **Решение:** Сохранить семантику поля `last_record` в `SessionFileSnapshot` (первый валидный record с конца файла). **Причина:** это поле используется в контрактных тестах CLI (`test_claude_cli_contract.py`, `test_codex_cli_contract.py`) и менять его сейчас — лишний риск.
- **Решение:** Зафиксировать превышение порога файла (512 строк против 500), а не разбивать модуль здесь же. **Причина:** разбиение reader'а на части — отдельная задача рефакторинга, она не связана с фиксом silence mode, и пользователь согласился отложить.

## Проблемы и решения

- **Проблема:** В silence mode (режим тишины — глобальный флаг, при котором бот доставляет только финальные ответы Claude и подавляет промежуточные `thinking`/`progress`) пользователю всё равно приходило много сообщений с галочкой ✅. **Решение:** Нашёл два источника `is_final=True`: handle_claude_result (реальный финал) и session_watcher (по флагу `is_turn_active`). Watcher ошибочно ставил `is_final=True` после каждого user-tool_result. Переписал критерий через reverse-скан до `result`-event'а.

## Контекст для следующей сессии

- **UX-эффект после фикса.** В обычном режиме промежуточные тексты Claude между шагами с инструментами теперь корректно показываются как `is_final=False` (курсивом с ⏳), а не с галочкой ✅. В silence mode пользователь видит только реальные финалы. Сообщения от фоновых сессий: пока в фоновой сессии Claude работает — приходят как промежуточные; когда закрывается turn (в `.jsonl` появляется `result`-event) — приходит финал ✅.
- **Превышение порога 500 строк.** `claude_code_session_file_reader.py` стал 512 строк. Это технический долг — модуль про одну задачу (чтение JSONL Claude), но физический порог пробит. План разбиения, если понадобится: вынести `_read_cursor_count_last_record_and_turn_active` и `_compute_is_turn_active_from_parsed_records` в отдельный модуль `claude_code_session_turn_state.py`, оставив в основном файле path-encoding, listing и snapshot reader.
- **Связь с ADR от 28-05.** Сегодняшний фикс продолжает линию борьбы с дублированием финалов. Тот ADR убирал дубль progress+final в `process_manager._process_events` (когда последний assistant.text совпадает с result.result). Сегодняшний — убирает ложные финалы из watcher (когда turn ошибочно считается закрытым после tool_result). Если пользователь дальше сообщит о новых случаях ложных финалов — стоит сначала проверить, какой из двух источников их шлёт.

## Результаты тестирования

- Юнит-тесты Claude backend: 23 passed (включая 2 новых RED → GREEN).
- Полный прогон `pytest tests/ -v`: 1020 passed, 4 skipped, 0 failed, 3 warnings (warnings от `python-telegram-bot` про `retry_after`, к фиксу не относятся).
- TDD-цикл подтверждён: до правки в `claude_code_session_file_reader.py` оба новых теста падали с `is_turn_active=False`; после правки — `True`, как ожидается.

## Выполненные команды

- `python -m pytest tests/test_claude_code_backend.py -v -k "turn_active_after_tool_result"` — RED, два теста падают.
- `python -m pytest tests/test_claude_code_backend.py -v -k "turn_active_after_tool_result or counts_records_and_activity or marks_assistant_last_as_active"` — GREEN после фикса.
- `python -m pytest tests/ -x --no-header -q` — полный прогон 1020 passed.
- `wc -l src/claude_manager/claude_code_session_file_reader.py` — 512 строк, проверка размера файла.
