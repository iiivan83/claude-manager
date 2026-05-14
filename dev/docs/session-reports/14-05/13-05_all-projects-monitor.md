# Session Report: All Projects Monitor

## Коротко

Добавлен глобальный режим `/all`: бот показывает сообщения из сессий всех проектов, а не только текущего проекта. Каждое сообщение начинается с кликабельной команды вида `/3s12 project-name`, которая переключает пользователя сразу в проект `3` и сессию `12`.

## Что изменилось

- **`src/claude_manager/all_projects_monitor.py`** — новый all-monitor с отдельными cursor-ами доставки. Он сканирует все проекты из `PROJECTS_ROOT_DIR`, но не двигает состояние обычного watcher-а.
- **`src/claude_manager/bot.py`** — `/projects` теперь показывает `/all all`; `/all` включает глобальный режим; добавлен обработчик `/3s12` и отправка all-сообщений с проектом и сессией в начале.
- **`src/claude_manager/project_manager.py`** — добавлен публичный сбор pending-сообщений для случая, когда пользователь выходит из all-режима в уже активный проект.
- **`src/claude_manager/claude_interaction.py`** — предупреждение мониторинга теперь просит сначала выбрать проект и сессию.
- **`dev/docs/brd/brd-user-journeys.md`** — обновлены CJM-07 и сценарий `/projects`.

## Решения

- **Отдельный all-monitor вместо расширения обычного watcher-а.** Обычный watcher помечает сообщения доставленными для текущего проекта. Для `/all` это нельзя делать, потому что сообщения должны прийти повторно после входа в проект.
- **Команда `/3s12` вместо двух отдельных кликов.** Telegram делает кликабельной одну slash-команду, поэтому номер проекта и номер сессии кодируются в одном токене.
- **Сообщения из `/all` остаются pending.** При первой all-доставке сохраняется unread snapshot, но он не очищается. Очистка происходит только после обычной pending-доставки при входе в проект.
- **`/new` заблокирован в all-режиме.** Иначе бот мог бы создать скрытую сессию в текущем проекте, хотя пользователь находится в глобальном режиме.

## Проверки

- **Targeted check:** `.venv/bin/pytest tests/test_all_projects_monitor.py tests/test_bot.py::TestHandleAll tests/test_bot.py::TestHandleMessage::test_handle_message_in_all_projects_mode_mentions_project tests/test_bot.py::TestSendAllProjectsWatcherMessage tests/test_bot.py::TestHandleProjects tests/test_bot.py::TestHandleSwitchProjectSession -q` — 15 passed.
- **Affected modules check:** `.venv/bin/pytest tests/test_all_projects_monitor.py tests/test_bot.py tests/test_project_manager.py tests/test_claude_interaction.py tests/test_media_group_handler.py -q` — 211 passed.
- **Non-E2E full check:** `.venv/bin/pytest tests --ignore=tests/e2e -q` — 982 passed, 1 skipped, 3 warnings. Warnings are from `python-telegram-bot` about future `retry_after` type changes.
- **Syntax check:** `.venv/bin/python -m compileall src/claude_manager tests/test_all_projects_monitor.py tests/test_bot.py tests/e2e/test_project_switching.py -q` — clean.

## Риски и ограничения

- **E2E не запускались.** Не-E2E набор и компиляция E2E-файла прошли; живой Telegram-сценарий `/all` нужно проверить после перезапуска бота.
- **Нумерация all-сессий использует daily registry проекта, если он есть.** Для сессий без записи в `daily_sessions.json` all-monitor выдаёт временный номер по списку файлов и держит in-memory link для клика.
- **Рабочее дерево было грязным до задачи.** Уже были изменения в `dev/docs/docs-index.md` и untracked отчёт `dev/docs/session-reports/13-05/14-53_restart-active-child-sessions-bug.md`. Они не относятся к этой фиче.

## Продолжение

- Перезапустить Claude Manager, чтобы живой бот поднял новый all-monitor.
- В Telegram проверить `/projects` → `/all` → сообщение из другого проекта → клик `/3s12`.
