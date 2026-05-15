# Bugfix: /stop не останавливал retry loop

**Дата:** 13.04.2026
**Пайплайн:** universal-bug-fixer
**Статус:** исправлено, верифицировано

## Что было сломано

Команда `/stop` в Telegram-боте убивала процесс Claude, но не останавливала цикл повторных попыток. После `/stop` пользователь видел "Claude остановлен", но через секунду бот писал "Ошибка Claude, повтор N/10..." и запускал новый процесс с тем же сообщением. Получался бесконечный цикл: `/stop` убивает процесс -> retry видит ошибку -> retry запускает новый процесс -> пользователь отправляет `/stop` снова -> и так далее.

## Что нашли (3 отклонения)

### DEV-1 — корневая причина: stop_event удалялся слишком рано

`stop_process()` в `process_manager.py` устанавливал флаг отмены (`stop_event.set()`), но сразу после этого удалял объект из словаря `_stop_events` через `pop()`. Retry loop проверял флаг через `_check_stop_requested()` — но объекта уже не было в словаре, проверка возвращала `None`, и `ProcessStoppedError` не бросался.

**Фикс:** убрали `_stop_events.pop()` из `stop_process()`. Очистка `stop_event` теперь происходит в `finally`-блоке `send_message()` — когда retry loop уже завершился.

### DEV-2 — зомби-процессы были неуправляемыми

`_restart_process()` создавал новый процесс Claude (`_processes[session_id]`), но не создавал для него управляющие структуры — `_stop_events` и `_busy_flags`. Если retry loop запускал новый процесс, а пользователь отправлял повторный `/stop` — некуда было установить флаг отмены.

**Фикс:** добавили создание `_busy_flags[session_id] = True` и `_stop_events[session_id] = asyncio.Event()` в `_restart_process()`, по аналогии с `create_process()`.

### DEV-3 — повторный /stop не работал

`handle_stop()` в `bot.py` проверял `has_process()` перед вызовом `stop_process()`. После первого `/stop` запись удалялась из `_processes`, и повторный `/stop` показывал "нечего останавливать" — хотя retry loop всё ещё работал в фоне.

**Фикс:** изменили условие: теперь `/stop` работает, если процесс есть (`has_process()`) ИЛИ если обработка активна (`is_busy()`).

## Тесты

- **9 whitebox тестов** — прямая проверка каждого фикса
- **6 blackbox тестов** — сценарии "остановка во время retry", "двойной /stop", "полный цикл без зомби"
- **135 существующих тестов** — без регрессий
- **Итого: 150 тестов, все проходят**

Тесты добавлены в проект:
- `tests/test_stop_triggers_retry_whitebox.py`
- `tests/test_stop_triggers_retry_blackbox.py`

## Изменённые файлы

- `src/claude_manager/process_manager.py` — DEV-1 (удаление pop из stop_process, cleanup в send_message finally), DEV-2 (инициализация словарей в _restart_process)
- `src/claude_manager/bot.py` — DEV-3 (условие в handle_stop)

## Backup

Ветка для отката: `bugfix/13.04_15.52-stop-triggers-retry-backup`
