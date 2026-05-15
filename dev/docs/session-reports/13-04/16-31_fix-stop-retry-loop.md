# Сессия 13-04: fix-stop-retry-loop

## Резюме

Исправлен баг: команда `/stop` не останавливала retry loop — после `/stop` бот перезапускал процесс Claude с тем же сообщением. Найден каскад из 3 дефектов в `process_manager.py` и `bot.py`, все исправлены, покрыты 15 тестами, 150/150 зелёные.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `src/claude_manager/process_manager.py` | изменён | DEV-1: убран `_stop_events.pop()` из `stop_process()` (строка 525), stop_event остаётся в словаре для retry loop. Очистка перенесена в `finally` блок `send_message()` (строка ~470). DEV-2: в `_restart_process()` добавлено создание `_busy_flags[session_id] = True` и `_stop_events[session_id] = asyncio.Event()` внутри `_busy_lock` (после строки 362) |
| `src/claude_manager/bot.py` | изменён | DEV-3: условие в `handle_stop()` (строка 709) изменено с `if not has_process()` на `if not has_process() and not is_busy()` — повторный `/stop` работает при активном retry loop |
| `tests/test_process_manager.py` | изменён | Обновлён существующий тест `test_multiple_processes_all_stopped` — теперь ожидает что stop_events сохраняются после `stop_process()` |
| `tests/test_stop_triggers_retry_whitebox.py` | создан | 9 whitebox тестов: 3 на DEV-1 (stop_event выживает stop_process), 4 на DEV-2 (_restart_process создаёт контрольные структуры), 2 на DEV-3 (handle_stop при retry) |
| `tests/test_stop_triggers_retry_blackbox.py` | создан | 6 blackbox тестов: остановка во время retry wait/event processing, двойной stop после restart, полный цикл без зомби-процессов |

## Коммиты

- `7719471` — fix(stop-command): /stop полностью прерывает retry loop — устранение каскада из 3 дефектов

## Выполненные команды

- `.venv/bin/python -m pytest tests/test_process_manager.py tests/test_bot.py tests/test_stop_triggers_retry_whitebox.py tests/test_stop_triggers_retry_blackbox.py -v` — финальная верификация всех 150 тестов, все зелёные
- `git checkout -b bugfix/13.04_15.52-stop-triggers-retry-backup` — backup ветка перед фиксом

## Решения

- **Решение**: stop_event не удаляется в `stop_process()`, а очищается в `finally` блока `send_message()`. **Причина**: retry loop должен видеть флаг отмены через `_check_stop_requested()` и `_wait_with_stop_check()` — если удалить объект раньше, проверка возвращает None.
- **Решение**: DEV-4 (edge case со сменой session_id и зависшим busy-флагом) пропущен. **Причина**: крайне маловероятный сценарий, не связан с основным багом, рекомендован как отдельная задача.
- **Решение**: использован пайплайн universal-bug-fixer (10 этапов). **Причина**: запрос пользователя, систематический подход с верификацией.

## Контекст для следующей сессии

- Фикс закоммичен в main, backup ветка `bugfix/13.04_15.52-stop-triggers-retry-backup`
- DEV-4 (edge case: `send_message.finally` может оставить зависший `_busy_flags` при смене `session_id` temp→real если `/stop` вызван для старого ID) — не исправлен, отложен
- Артефакты пайплайна: `dev/docs/logs/bugfix/13.04_15.52-universal-bug-fixer-stop-triggers-retry/` (orchestrator-log.json, 9 agent-outputs, report.md)
- Бот нужно перезапустить чтобы фикс вступил в силу
