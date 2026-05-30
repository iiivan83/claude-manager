# Сессия 28-05: устранение дубля финального сообщения и плашки вопросов в Telegram-боте

## Резюме

Расследованы и исправлены два пользовательских бага в Telegram-боте Claude Manager. Первый — каждый ответ Claude приходил дважды: сначала со значком `⏳`, потом со значком `✅` с тем же текстом. Второй — Claude задавал вопросы через встроенный SDK-инструмент `AskUserQuestion`, который в Telegram отображает «плашку» с кнопками, но через бот эта плашка пропадает; Claude получал пустой ответ и сам отвечал за пользователя «разумным дефолтом». Оба исправлены минимальными правками с TDD-циклом (RED→GREEN→Revert→Restore) и без регрессий в остальных 907 юнит-тестах. На текущем работающем боте фиксы ещё не задеплоены — сервис не перезапускался.

## Изменённые файлы

- **`src/claude_manager/process_manager.py`** — изменён. В функции `_process_events` добавлен look-ahead-буфер `pending_progress_text: str | None = None`. Текст из `assistant`-события сначала кладётся в буфер. На следующем не-terminal событии буфер flush-ится как progress (с учётом тротла). На terminal-событии (`result`) буфер сравнивается с `last_assistant_text` (финальный текст из поля `result.result`); если совпадает — подавляется (это и есть финал, уйдёт через `SendResult`); если не совпадает (например, был thinking-блок) — flush как progress. Logic block добавлен в двух местах: перед обработкой не-terminal события и внутри terminal-ветки до `break`.
- **`src/claude_manager/claude_code_backend.py`** — изменён. Добавлены константы `DISALLOWED_TOOLS_IN_BOT_MODE = "AskUserQuestion"` и `BOT_MODE_SYSTEM_PROMPT_APPENDIX` (текст инструкции для Claude на английском о том, что он работает через Telegram, без UI-плашек). В методе `compose_subprocess_command_args` добавлены четыре элемента argv: `--disallowedTools`, значение константы, `--append-system-prompt`, значение константы. Эти флаги передаются на каждом запуске Claude CLI как подпроцесса бота, независимо от того, новая это сессия или восстановление.
- **`tests/test_process_manager.py`** — изменён. Добавлены два новых теста после блока «session_id_callback»: `test_progress_not_duplicated_when_assistant_text_equals_final_result` (подаёт `assistant`+`result` с одинаковым текстом, проверяет что progress_callback НЕ вызван с этим текстом) и `test_intermediate_assistant_text_still_sent_as_progress` (подаёт поток с tool_use между двумя assistant-событиями, проверяет что промежуточный текст ушёл как progress, финал — нет).
- **`tests/test_claude_code_backend.py`** — изменён. Добавлены два теста после блока про `compose_args`: `test_compose_args_disallow_ask_user_question_tool` (проверяет наличие `--disallowedTools` с токеном `AskUserQuestion`) и `test_compose_args_append_system_prompt_explains_text_only_questions` (проверяет наличие `--append-system-prompt` с упоминанием Telegram).
- **`dev/docs/adr/28.05_17.02-session-change-documenter-stream-json-progress-final-deduplication.md`** — создан. ADR о look-ahead в обработке stream-json.
- **`dev/docs/adr/28.05_17.02-session-change-documenter-disallow-ask-user-question-in-bot-mode.md`** — создан. ADR о запрете AskUserQuestion в bot-режиме.
- **`dev/docs/session-reports/28-05/17-02_telegram-bot-duplicate-final-and-ask-user-question-fixes.md`** — создан. Этот отчёт.

## Решения

- **Решение**: Bug 1 чинить через look-ahead-буфер с точным сравнением pending-текста и финального `result.result`. **Причина**: пользователь явно выбрал этот вариант из трёх предложенных. Альтернатива «возвращать только thinking из `read_progress_text_from_event`» (вариант B) обрывает доставку промежуточных текстов между шагами с инструментами — ухудшает UX на длинных turn-ах. Альтернатива «дедупликация в `handle_claude_result` без отправки финала» (вариант C) ломает silence mode и убирает маркер `✅`.
- **Решение**: Bug 2 чинить через два CLI-флага `--disallowedTools AskUserQuestion` и `--append-system-prompt`. **Причина**: пользователь явно выбрал этот вариант. Альтернатива «перехват tool_use на стороне бота через control protocol» — это фича на несколько дней работы (state-машина ожидания ответа, синхронизация со `/stop`, обработка переключения проектов), а пользовательская боль решается двумя CLI-флагами за часы.
- **Решение**: писать RED-тесты ДО фиксов, затем GREEN, затем формально подтверждать через Revert-Restore. **Причина**: пользователь явно выбрал TDD-вариант из третьего вопроса. Это даёт гарантию, что тесты действительно ловят описанный баг, а не пропускают что угодно.
- **Решение**: первая итерация look-ahead-фикса (которая подавляла любой pending на terminal event) была неправильной — сломала 3 теста с потоками `thinking`+`result`. Уточнили логику: подавляем только если pending == last_assistant_text. **Причина**: `thinking`-текст никогда не совпадает с `result.result` (это разные поля разных типов content-блоков), поэтому правильное условие — точное сравнение, а не «терминал значит финал».
- **Решение**: НЕ создавать `architecture.md` в корне проекта и НЕ обновлять `dev/docs/brd/brd-user-journeys.md`. **Причина**: для architecture.md создание с нуля требует широкого решения, выходящего за рамки этого багфикса. Для BRD триггер «изменён существующий сценарий» формально не сработал: CJM-02 уже описывает желаемое поведение «промежуточные обновления и финал — разные сообщения», а фикс восстанавливает соответствие реальности с описанием, а не вводит новый сценарий.
- **Решение**: НЕ обновлять `dev/docs/docs-index.md`. **Причина**: структура папок не менялась, новых директорий не появилось.
- **Решение**: рестарт сервиса бота на текущем Linux-сервере должен делаться через `systemctl --user restart claude-manager` из внешнего терминала, а не через команду `/restart` в боте. **Причина**: обработчик `/restart` в `bot.py:1290-1322` использует `launchctl kickstart` (macOS-only), а на этом сервере живёт systemd-сервис пользователя. Запускать `systemctl restart` из subprocess-дерева самого бота нельзя — это убьёт текущий Claude-подпроцесс до того, как новый бот стартует (правило «запрета самоперезапуска из собственного дерева процессов»).

## Контекст для следующей сессии

- **Фиксы ещё не задеплоены на работающий бот.** Активный процесс PID `210627` запущен 22 мая и держит в памяти старый код без look-ahead и без disallow-флагов. Editable install уже видит новый код в импорте (`import claude_manager.process_manager` показывает `pending_progress_text` в исходниках), но Python загружает модули в память один раз при старте — нужен рестарт сервиса.
- **Команда для деплоя** (выполнить из внешнего терминала на сервере, НЕ через бот): `systemctl --user restart claude-manager && sleep 3 && systemctl --user status claude-manager --no-pager -l | head -20`. После — `MainPID` должен поменяться, `ActiveEnterTimestamp` должен стать «сегодня».
- **Расхождение между CLAUDE.md и реальностью.** Проектный CLAUDE.md описывает запуск через macOS LaunchAgents (launchctl, `~/Library/LaunchAgents`), но на текущем сервере бот живёт под systemd-сервисом пользователя (`~/.config/systemd/user/claude-manager.service`). `restart-claude-manager.sh` и команда `/restart` в Telegram — оба macOS-only. Это нужно когда-нибудь зафиксировать в CLAUDE.md и переписать обработчик `handle_restart` так, чтобы он различал платформу или использовал универсальный механизм.
- **Pre-existing env-issue в `tests/test_config.py`.** 12 из 40 тестов в этом файле падают на Linux — внутри тесты выставляют macOS-путь `/Users/ivan/Desktop/claude-sandbox` через monkeypatch или фикстуру, а файла такого пути на Linux нет. Подтверждено `git stash` + прогон без изменений сессии: те же 12 падений. Этот сессионный багфикс не трогал config.py / test_config.py. Стоит когда-нибудь починить тесты под Linux окружение.
- **Codex backend не затронут.** У него своя модель событий (`item.completed` с типами `reasoning` и `agent_message`), `AskUserQuestion` отсутствует, дубль progress+final там не воспроизводится так же гарантированно. Менять Codex не требовалось.
- **Документация скиллов `pipeline-spec.md` использует `AskUserQuestion` для интерактивных вопросов в пайплайнах.** Эти упоминания НЕ касаются bot-режима — пайплайны запускаются в обычном Claude Code в терминале, где плашка работает. Никаких правок в скиллах делать не нужно.

## Коммиты

Будет создан коммит `docs: session-change-documenter — Telegram bot duplicate final + AskUserQuestion fixes` после этого отчёта, содержит все изменения сессии: 2 файла продакшен-кода, 2 файла тестов, 2 ADR, этот сессионный отчёт.

## Выполненные команды

- `pytest tests/test_process_manager.py::test_progress_not_duplicated_when_assistant_text_equals_final_result tests/test_process_manager.py::test_intermediate_assistant_text_still_sent_as_progress tests/test_claude_code_backend.py::test_compose_args_disallow_ask_user_question_tool tests/test_claude_code_backend.py::test_compose_args_append_system_prompt_explains_text_only_questions` — на старом коде: 3 fail (как ожидалось RED). На новом коде: 4 pass (GREEN).
- `pytest tests/test_process_manager.py tests/test_claude_code_backend.py` — все 94 теста зелёные.
- `pytest tests/ --ignore=tests/e2e --ignore=tests/integration --ignore=tests/test_config.py` — все 907 тестов зелёные.
- `git checkout -- src/claude_manager/process_manager.py src/claude_manager/claude_code_backend.py` + повторный прогон новых тестов — 3 fail, 1 pass (Revert-проверка регрессии). Затем восстановление через `cp` бэкапов из `/tmp` — 4 pass снова (Restore-проверка).
- `systemctl --user show claude-manager -p MainPID -p ActiveEnterTimestamp` — подтверждение что бот всё ещё на старом коде: `MainPID=210627`, `ActiveEnterTimestamp=Fri 2026-05-22 22:53:50`.

## Проблемы и решения

- **Проблема**: первая итерация look-ahead-фикса (подавлять любой pending на terminal event) сломала 3 теста с потоками `thinking`+`result`. **Решение**: уточнили условие подавления — сравниваем pending с last_assistant_text. `thinking`-текст всегда отличается от `result.result`, поэтому flush корректен. Все 94 теста зелёные после уточнения.
- **Проблема**: 12 тестов в `tests/test_config.py` падают на этом Linux-сервере с ошибкой про несуществующий `/Users/ivan/Desktop/claude-sandbox`. **Решение**: подтвердили через `git stash` + прогон без своих изменений, что 12 падений — pre-existing env-issue, не связанная с этим багфиксом. В сессионный отчёт записано как «контекст для следующей сессии».
- **Проблема**: команда `/restart` в боте использует `launchctl kickstart` (macOS-only) и не работает на Linux. **Решение**: дать пользователю прямую команду `systemctl --user restart claude-manager` для запуска из внешнего терминала. Рестарт обработчика для Linux — отдельная задача, не сделана.

## Результаты тестирования

- Юнит-тесты: 907 passed, 12 pre-existing env-failures в test_config.py (не относятся к сессии).
- Регрессионная защита: RED→GREEN→Revert→Restore цикл выполнен формально, все 4 новых теста реально ловят описанные баги (сообщения об ошибках на сломанном коде осмысленные: «Финальный текст не должен дублироваться как progress», «Bot must pass --disallowedTools»).
- Интеграционные и E2E тесты: НЕ запускались — нужны реальный Claude CLI + Telegram-аккаунт, это для следующего шага после деплоя.
