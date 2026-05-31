# Сессия 31-05: персистентный reply-route registry

## Резюме

Исправлен сценарий address-reply после `/restart`: бот больше не забывает маршруты reply-сообщений сразу после перезапуска. Reply-route registry теперь сохраняет последние 200 маршрутов на диск, загружает их при старте и не даёт unknown reply на сообщение бота молча уйти в активную сессию.

## Изменённые файлы

- **`src/claude_manager/reply_route_registry.py`** — изменён — добавлены JSON-сериализация, загрузка, сохранение, bounded pruning и лимит последних 200 маршрутов
- **`src/claude_manager/telegram_lifecycle_handlers.py`** — изменён — `post_init()` загружает persisted reply routes при старте бота
- **`src/claude_manager/reply_route_handler.py`** — изменён — unknown reply на сообщение текущего бота больше не угадывает активную сессию
- **`tests/test_reply_route_registry.py`** — изменён — добавлены проверки reload persisted route и удаления самой старой записи при 201-м маршруте
- **`tests/test_reply_route_handler.py`** — изменён — добавлен regression-тест unknown bot reply внутри проекта
- **`tests/test_reply_route_handler_background.py`** — изменён — добавлен regression-тест быстрого подтверждения после reload route registry
- **`tests/test_telegram_lifecycle_handlers.py`** — изменён — добавлена проверка загрузки reply-route registry в `post_init()`
- **`dev/docs/brd/brd-user-journeys.md`** — изменён — CJM-17 теперь описывает восстановление route с диска и лимит 200 записей
- **`dev/docs/specs/31.05_04.56-reply-routing-v1-spec.md`** — изменён — persistent route registry больше не указан как нецель v1; добавлен лимит 200 маршрутов
- **`dev/docs/adr/project_architecture.md`** — изменён — архитектурное описание reply-route registry обновлено с in-memory-only на bounded persisted registry
- **`dev/docs/adr/31.05_12.22-session-change-documenter-persistent-bounded-reply-route-registry.md`** — создан — фиксирует архитектурное решение по persisted bounded registry
- **`dev/docs/session-reports/31-05/12-22_reply-route-persistent-registry.md`** — создан — отчёт этой сессии

## Решения

- **Решение**: сохранять reply-route registry в `~/.local/state/claude-manager/reply_routes.json`. **Причина**: штатный `/restart` раньше стирал in-memory route-карту, и reply на старое сообщение бота терял адресацию
- **Решение**: хранить только последние 200 маршрутов. **Причина**: Иван явно выбрал bounded-подход без бесконечного роста файла; при добавлении новых записей самые старые должны удаляться
- **Решение**: unknown reply на сообщение текущего бота не отдавать обычной обработке текста. **Причина**: иначе бот может молча отправить текст в активную сессию, хотя пользователь отвечал на конкретное старое сообщение
- **Решение**: не вводить TTL по времени. **Причина**: для текущей задачи достаточно простого окна в 200 записей; оно предсказуемее и проще в проверке

## Проблемы и решения

- **Проблема**: после `/restart` route registry был пустым, поэтому reply не попадал в ветку `reply_route_handler.py`. **Решение**: добавлены persistent load/save и загрузка в `post_init()`
- **Проблема**: старые сообщения до включения persisted registry невозможно восстановить надёжно. **Решение**: handler теперь показывает unknown-route сообщение для reply на сообщение текущего бота, а не угадывает активную сессию
- **Проблема**: `reply_route_handler.py` уже превысил 300 строк. **Решение**: зафиксирован size-warning; следующий шаг декомпозиции — вынести фоновую dispatch-логику или unknown-route helpers в отдельный модуль

## Результаты тестирования

- **`.venv/bin/python -m pytest tests/test_reply_route_registry.py -q`** — 5 passed
- **`.venv/bin/python -m pytest tests/test_reply_route_handler.py tests/test_reply_route_handler_error_results.py tests/test_reply_route_handler_background.py tests/test_reply_route_registry.py tests/test_telegram_lifecycle_handlers.py::TestPostInit::test_post_init_loads_reply_route_registry -q`** — 21 passed
- **`.venv/bin/python -m pytest tests/ -q --ignore=tests/e2e`** — 1138 passed, 5 skipped, 3 warnings
- **`git diff --check`** — без ошибок
- **`.venv/bin/python -m compileall -q src/claude_manager`** — без ошибок

## Контекст для следующей сессии

Live-бот ещё нужно перезапустить через `/restart`, чтобы он подхватил persisted route registry и лимит 200 записей. Из текущего subprocess рестарт не запускался из-за проектного запрета на самоперезапуск из дерева процессов бота.

Маршруты для сообщений, отправленных до этой правки, не появятся в `reply_routes.json` задним числом. Для таких сообщений бот теперь должен показать понятную ошибку, если Иван отвечает именно на сообщение текущего бота.

`src/claude_manager/reply_route_handler.py` сейчас 340 строк. Это warning-порог, но не stop-порог; при следующей правке reply-routing лучше первым шагом вынести фоновую dispatch-логику в отдельный модуль.
