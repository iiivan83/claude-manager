# Context and Architecture Spec Review

Статус: `success`.

Я проверил активные документы как потенциально восстановленные копии: сначала сверил их с BRD, dependency graph и очередью реализации, затем сравнил с текущим кодом и тестами. Это не ревью вкуса текста. Вывод ниже основан на том, что уже реализовано в `src/claude_manager/**`, что покрыто тестами, и какие документы теперь расходятся с этим состоянием.

Файл эталонного скилла `.claude/skills/spec-creator/SKILL.md` отсутствует. Поэтому я применил общие критерии качества спецификации: её можно реализовать без дополнительных вопросов, она покрывает граничные случаи, не противоречит архитектуре и даёт проверяемый план.

Полный запуск тестов я не повторял: использованы результаты оркестратора `~/.venvs/claude-manager/bin/python -m pytest tests/ -q` с итогом `961 passed, 1 skipped, 3 warnings`, а также backend-aware набор с итогом `451 passed`.

## Главный вывод

Backend-aware цепочка в коде в целом реализована. В коде есть абстракция `CodingAgentBackend`, адаптеры Claude и Codex, текущий backend для новых сессий, владение сессией через пару `(session_id, backend)`, backend-aware process lifecycle, watcher, project switching, Telegram-команда `/agent`, routing `/new`, `/N`, `/stop`, `/sessions`, и E2E-покрытие выбора агента.

Поэтому часть активных спецификаций не нужно ревьюить как незавершённые. Их надо классифицировать как `ready_to_move_to_realised`, с важным ограничением: в `dev/docs/specs/realised/` уже есть старые Claude-only файлы с некоторыми такими же именами, поэтому будущий перенос нельзя делать молчаливым overwrite.

## Ready To Move To Realised

- `dev/docs/specs/coding_agent_backend_spec.md`
- `dev/docs/specs/claude_code_backend_spec.md`
- `dev/docs/specs/codex_backend_spec.md`
- `dev/docs/specs/current_backend_registry_spec.md`
- `dev/docs/specs/session_watcher_spec.md`
- `dev/docs/specs/project_manager_spec.md`
- `dev/docs/specs/agent_backend_selection_user_journey_spec.md`

Причина: код и тесты подтверждают соответствующее поведение. Для `coding_agent_backend_spec.md` дополнительно подтверждено, что активная копия идентична уже лежащей в `dev/docs/specs/realised/coding_agent_backend_spec.md`.

`dev/docs/specs/module-dependency-graph.md` я использовал как контрольный архитектурный документ. Он совпадает с реализованным направлением архитектуры: текущий backend применяется только для новых сессий, существующие сессии принадлежат своему CLI, watcher работает по backend-инстанциям, а Telegram-facing слой подключён последним. Я не включил его в список переноса, потому что это не модульная спецификация реализации; найденный конфликт находится в BRD и очереди, а не в самом graph.

## Unfinished Specs To Review

- `dev/docs/brd/brd-user-journeys.md`
- `dev/docs/specs/codex_support_spec_implementation_order.md`
- `dev/docs/specs/daily_session_registry_spec.md`
- `dev/docs/specs/session_manager_spec.md`
- `dev/docs/specs/unread_buffer_spec.md`
- `dev/docs/specs/process_manager_spec.md`
- `dev/docs/specs/telegram_agent_backend_integration_spec.md`

Эти документы не надо считать полностью нереализованными. Проблема другая: они содержат контрактные расхождения с уже реализованным кодом или с актуальной архитектурной картой. До переноса в `realised` их нужно поправить, чтобы realised-документация не стала ложным источником правды.

## Findings

Подробные findings записаны в `context-and-architecture-spec-reviewer.findings.json`.

Коротко:

- `finding-001`: BRD и очередь реализации всё ещё описывают pre-/agent состояние, хотя `/agent` реализован и покрыт E2E.
- `finding-002`: `process_manager_spec.md` разрешает fallback для `backend=None`, но код и тесты требуют явный backend для backend-aware turn-а.
- `finding-003`: `telegram_agent_backend_integration_spec.md` одновременно допускает legacy runner compatibility и требует полного отсутствия Claude-specific parsing в `claude_runner.py`.
- `finding-004`: storage-спеки говорят, что старые API удалены, а код и тесты сохраняют compatibility wrappers.

## Move Caution

При будущем переносе в `dev/docs/specs/realised/` нельзя молча перезаписывать старые Claude-only версии:

- `dev/docs/specs/realised/daily_session_registry_spec.md`
- `dev/docs/specs/realised/session_manager_spec.md`
- `dev/docs/specs/realised/process_manager_spec.md`
- `dev/docs/specs/realised/session_watcher_spec.md`

Для готовых backend-aware спек с совпадающими именами нужен явный конфликт-безопасный шаг: либо заранее переименовать/архивировать старую Claude-only версию, либо сохранить обе версии с понятной датой/суффиксом.
