# Implementation Risk Spec Review

## Короткий вывод

Ревью завершено после сравнения активных спецификаций с текущим кодом и тестами. Это не отсутствие ревью: реализованные спеки не разбирались как незавершенные, а были отделены как `ready_to_move_to_realised`, то есть готовые к переносу в папку реализованных спецификаций.

Незавершенной осталась только `dev/docs/specs/project_manager_spec.md`. В ней есть один реальный user-facing пробел: команды `/next` и `/prev` описаны в спецификации, но не подключены в Telegram-боте. Дополнительно в этой же спеки есть восстановленный пример pending delivery, который не передает `backend`, хотя код уже делает это правильно.

## Эталон качества

Файл `.claude/skills/spec-creator/SKILL.md` отсутствует. Проверка `.agents/skills/spec-creator/SKILL.md` тоже дала `missing`. Поэтому ревью выполнено по общим критериям из задачи: спека должна быть реализуема без дополнительных вопросов, покрывать edge cases, не спорить с текущей архитектурой и давать проверяемый план реализации.

## Что сверено с кодом

Backend-контракт реализован в `src/claude_manager/coding_agent_backend.py`: есть `BackendName`, `SessionFileSnapshot`, `StopStrategy`, terminal snapshot и фабрика backend-ов. `src/claude_manager/claude_code_backend.py` и `src/claude_manager/codex_backend.py` реализуют разные CLI-контракты без смешивания ownership.

State-слой реализован backend-aware: `current_backend_registry`, `daily_session_registry`, `session_manager` и `unread_buffer` хранят backend там, где это нужно для выбора агента, нумерации сессий, активной сессии и pending-снапшотов.

Runtime-слой реализован backend-aware: `process_manager` держит процесс с backend-владением, применяет stop strategy из backend-а, передает backend в callbacks и защищает session ownership. `session_watcher` работает по backend-ам и держит finality через buffer-and-hold.

Telegram-интеграция реализована для `/agent`, `/new`, `/sessions`, `/stop`, выбора сессии и pending delivery. Это подтверждается кодом в `src/claude_manager/bot.py`, тестами `tests/test_bot.py` и E2E-тестом `tests/e2e/test_agent_backend_selection.py`.

Проверки, уже выполненные оркестратором, достаточны для классификации готовых спек: полный набор `tests/` прошел как `961 passed, 1 skipped`, а backend-aware набор прошел как `451 passed`.

## Ready To Move To Realised

Эти активные спеки уже фактически реализованы и не должны ревьюиться как незавершенные:

- `dev/docs/specs/coding_agent_backend_spec.md`
- `dev/docs/specs/claude_code_backend_spec.md`
- `dev/docs/specs/codex_backend_spec.md`
- `dev/docs/specs/current_backend_registry_spec.md`
- `dev/docs/specs/daily_session_registry_spec.md`
- `dev/docs/specs/session_manager_spec.md`
- `dev/docs/specs/unread_buffer_spec.md`
- `dev/docs/specs/process_manager_spec.md`
- `dev/docs/specs/session_watcher_spec.md`
- `dev/docs/specs/telegram_agent_backend_integration_spec.md`
- `dev/docs/specs/agent_backend_selection_user_journey_spec.md`

## Unfinished Specs Reviewed

- `dev/docs/specs/project_manager_spec.md`

Эта спека не готова к переносу целиком. Core-часть project manager реализована, включая backend-aware `PendingDeliveryItem`, сбор pending-сообщений и тесты соседних проектов. Но команды `/next` и `/prev` описаны как пользовательское поведение и не подключены в Telegram-слое.

## Context Specs

`dev/docs/specs/codex_support_spec_implementation_order.md`, `dev/docs/specs/module-dependency-graph.md` и `dev/docs/brd/brd-user-journeys.md` использованы как контекст для проверки трассировки и порядка работ. Я не классифицировал их как незавершенные module specs, потому что они не являются отдельной реализационной единицей уровня backend/session/process/watcher/bot handler.

## Findings

### finding-001 - high

Команды `/next` и `/prev` описаны в `project_manager_spec.md`, но не подключены в Telegram-боте.

Доказательства:

- `dev/docs/specs/project_manager_spec.md:92` требует `CommandHandler("next", ...)`.
- `dev/docs/specs/project_manager_spec.md:97` требует `CommandHandler("prev", ...)`.
- `src/claude_manager/project_manager.py:190` реализует только нижнеуровневый `resolve_neighbor_project`.
- `src/claude_manager/bot.py:68` не включает `/next` или `/prev` в `BOT_COMMANDS`.
- `src/claude_manager/bot.py:1029` не регистрирует обработчики `next` или `prev`.
- `tests/test_project_manager.py:813` покрывает внутреннюю функцию, но не Telegram-команду.

Рекомендация: дореализовать команды в `src/claude_manager/bot.py` и добавить bot-level тесты в `tests/test_bot.py`. Это малая правка, потому что выбор соседнего проекта уже реализован.

### finding-002 - medium

В `project_manager_spec.md` пример pending delivery вызывает `send_response` без `backend`, хотя `PendingDeliveryItem` уже содержит backend, а текущий код передает его явно.

Доказательства:

- `dev/docs/specs/project_manager_spec.md:88` вызывает `send_response(chat_id, item.text, day_number, is_final=item.is_final)`.
- `dev/docs/specs/project_manager_spec.md:120` описывает `PendingDeliveryItem.backend`.
- `src/claude_manager/bot.py:255` показывает, что `send_response` имеет default на `BackendName.CLAUDE`.
- `src/claude_manager/bot.py:732` показывает фактический правильный вызов с `backend`.

Рекомендация: исправить пример в `project_manager_spec.md`, чтобы он передавал `item.backend` в `send_response`. Это не требование к коду, а устранение риска в активной незавершенной спеки.
