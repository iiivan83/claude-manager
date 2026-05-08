# Сводное решение по findings

Сведены два набора замечаний от `context-and-architecture-spec-reviewer` и `implementation-risk-spec-reviewer`.

Оставлены только замечания, которые реально блокируют или делают рискованным перенос/реализацию незавершённых спецификаций. Дубли, вкусовые замечания и замечания без проверяемого риска удалены.

## Findings

- **finding-001, high** — `project_manager_spec.md` обещает пользовательские команды `/next` и `/prev`, но `bot.py` не регистрирует эти команды и нет bot-level тестов. Спеку нельзя переносить в `realised` без реализации команд или явного выноса команд в будущий scope.
- **finding-002, high** — `process_manager_spec.md` описывает fallback backend=None через `current_backend_registry`, а код backend-aware path требует явный backend. Это опасно для переноса, потому что может задокументировать нарушение ownership существующих сессий.
- **finding-003, medium** — `project_manager_spec.md` в примере pending delivery вызывает `send_response()` без backend, хотя `PendingDeliveryItem` backend хранит и код передаёт его явно.
- **finding-004, medium** — storage specs требуют удалить старые API, но код и тесты сохраняют compatibility wrappers/no-op paths. Перед переносом нужно либо описать wrappers, либо действительно удалить их с тестами.
- **finding-005, medium** — `telegram_agent_backend_integration_spec.md` одновременно допускает временный legacy `ClaudeProcess` и требует полного отсутствия Claude-specific parsing в `claude_runner.py`. Критерий готовности нужно сузить под новый backend-aware path.

## Ready to move

Эти спеки можно переносить в `dev/docs/specs/realised/` при соблюдении conflict-safe notes из JSON:

- `dev/docs/specs/coding_agent_backend_spec.md`
- `dev/docs/specs/claude_code_backend_spec.md`
- `dev/docs/specs/codex_backend_spec.md`
- `dev/docs/specs/current_backend_registry_spec.md`
- `dev/docs/specs/session_watcher_spec.md`
- `dev/docs/specs/agent_backend_selection_user_journey_spec.md`

## Move notes

- Для `coding_agent_backend_spec.md` в `realised` уже есть идентичный файл; перезапись не нужна.
- Для `session_watcher_spec.md`, `daily_session_registry_spec.md`, `session_manager_spec.md` и `process_manager_spec.md` в `realised` уже есть старые одноимённые версии. Их нельзя молча перезаписывать.
- `brd-user-journeys.md` и `codex_support_spec_implementation_order.md` устарели вокруг `/agent`, но в этом сведении они оставлены как контекст, а не как findings по незавершённым module specs.
