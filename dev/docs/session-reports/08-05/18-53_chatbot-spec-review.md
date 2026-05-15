# Сессия 08-05: ревью восстановленных backend-aware спецификаций чат-бота

## Резюме

Восстановленные backend-aware спецификации чат-бота сверены с уже реализованным кодом и тестами. Готовые спецификации перенесены в `dev/docs/specs/realised/`, спорные контракты согласованы с пользователем, найденные cross-spec расхождения исправлены, а BRD дополнен пользовательским сценарием `/agent`.

## Изменённые файлы

- **dev/docs/brd/brd-user-journeys.md** — изменён — добавлен CJM-16 для команды `/agent`, обновлена карта состояний и общий механизм владения сессии CLI-бэкендом
- **dev/docs/specs/realised/project_manager_spec.md** — изменён — зафиксировано, что `/next` и `/prev` не входят в текущий пользовательский контракт, а `resolve_neighbor_project(direction)` остаётся внутренним/future API
- **dev/docs/specs/realised/08.05_16.38-backend-aware-process_manager_spec.md** — изменён — закреплён явный `BackendName` для backend-aware `send_message`, отсутствие fallback к `current_backend_registry`, composite key `(session_id, backend)` и новый runner path через `start_subprocess_for_backend`
- **dev/docs/specs/realised/coding_agent_backend_spec.md** — изменён — уточнён общий backend-контракт, владение сессией и совместимость потребителей
- **dev/docs/specs/realised/current_backend_registry_spec.md** — изменён — уточнено, что реестр текущего backend-а используется верхним слоем для новых сессий, а не как fallback внутри `process_manager`
- **dev/docs/specs/realised/08.05_16.38-backend-aware-session_watcher_spec.md** — изменён — зафиксированы backend-aware operational scans через `list_all_session_files_for_project` и корректная финальность watcher-сообщений
- **dev/docs/specs/realised/claude_code_backend_spec.md** — изменён — уточнены backend-specific контракты Claude Code CLI и operational/full scan API
- **dev/docs/specs/realised/codex_backend_spec.md** — изменён — уточнены backend-specific контракты Codex CLI, stop strategy и operational/full scan API
- **dev/docs/specs/realised/telegram_agent_backend_integration_spec.md** — изменён — закреплено, что pending delivery передаёт backend в `send_response`, а legacy `ClaudeProcess` / `start_process` остаются compatibility debt
- **dev/docs/specs/realised/08.05_16.38-backend-aware-daily_session_registry_spec.md** — изменён — уточнены backend-aware записи дневного реестра и legacy compatibility wrappers
- **dev/docs/specs/realised/08.05_16.38-backend-aware-session_manager_spec.md** — изменён — уточнены backend-aware активные сессии, lazy migration и отсутствие прямого fallback к `current_backend_registry`
- **dev/docs/specs/realised/unread_buffer_spec.md** — изменён — уточнены backend-aware snapshots и legacy compatibility wrappers
- **dev/docs/specs/realised/agent_backend_selection_user_journey_spec.md** — изменён — сценарий `/agent` признан готовым и согласован с реализованной backend-aware цепочкой
- **dev/docs/logs/human-in-the-loop-spec-reviewer/08.05_16.38-human-in-the-loop-spec-reviewer-chatbot-spec-review/** — создано/изменено — сохранены артефакты reviewer-ов, merger-а, final verifier-а и локальной финальной проверки
- **dev/docs/session-reports/08-05/18-53_chatbot-spec-review.md** — изменён — предварительный отчёт приведён к обязательному формату session report и дополнен итогами документатора

## Решения

- **Решение**: `/next` и `/prev` не входят в текущий пользовательский контракт. **Причина**: пользователь отклонил реализацию этих команд; `resolve_neighbor_project(direction)` остаётся внутренней заготовкой для будущей доработки
- **Решение**: backend-aware `process_manager.send_message` требует явный `BackendName`. **Причина**: сессия должна продолжать работать через backend, который её создал; fallback к глобальному `current_backend_registry` мог бы отправить старую сессию не в тот CLI
- **Решение**: pending delivery передаёт backend в `send_response`. **Причина**: при возврате в проект бот должен зарегистрировать и показать непрочитанное сообщение с правильным владельцем сессии
- **Решение**: storage specs документируют legacy compatibility wrappers. **Причина**: старые Claude-only вызовы ещё могут существовать в тестах и переходном коде, но новый контракт остаётся backend-aware
- **Решение**: `ClaudeProcess` / `start_process` остаются compatibility debt, а новый runner path — `start_subprocess_for_backend` / `BackendSubprocess`. **Причина**: это сохраняет старую совместимость без смешивания её с основным backend-aware путём
- **Решение**: operational scans используют `list_all_session_files_for_project`, а capped `list_session_files_for_project` остаётся для UI `/sessions`. **Причина**: фоновые процессы не должны терять старые сессии из-за лимита показа последних 15 элементов
- **Решение**: сессионный отчёт обновлён вместо создания дубля. **Причина**: файл `dev/docs/session-reports/08-05/18-53_chatbot-spec-review.md` уже был создан вручную и покрывал эту же сессию

## Контекст для следующей сессии

- Коммит не делался, файлы в git-индекс не добавлялись: пользователь явно просил только запустить документатора
- В рабочем дереве есть чужие изменения, не связанные с документатором; их нельзя откатывать без отдельной просьбы пользователя
- Готовые backend-aware спеки лежат в `dev/docs/specs/realised/`; старые Claude-only realised-спеки не перезаписывались, конфликтующие новые файлы получили префикс `08.05_16.38-backend-aware-`
- BRD теперь описывает `/agent` как CJM-16, но старые упоминания CJM-13–15 в других спецификациях остаются отдельным вопросом: документатор не восстанавливал эти сценарии, потому что триггер этой сессии был только про `/agent`
- `docs-index.md` не обновлялся: структура папок не изменилась, а правила documenter-а прямо исключают обновление индекса при перемещении файлов внутри уже существующих папок

## Выполненные команды

- **Полный pytest до финальных doc-only правок** — `961 passed, 1 skipped, 3 warnings`
- **Targeted pytest после финальных правок** — `51 passed`
- **JSON validation** — артефакты reviewer-ов проверены через `python -m json.tool`
- **Локальные финальные text checks** — проверено отсутствие старого implicit backend fallback, recent-list operational scans и obsolete runner contract text

## Проблемы и решения

- **Проблема**: первый final verifier нашёл 3 high cross-spec расхождения. **Решение**: все три расхождения исправлены в реализованных спецификациях
- **Проблема**: повторный verifier завис без артефактов. **Решение**: зависший прогон остановлен, после этого выполнена локальная финальная проверка с сохранением артефактов
- **Проблема**: отдельный CLI-запуск `session-change-documenter` обновил документы, но завис без финального ответа в чат. **Решение**: процесс остановлен вручную, изменения проверены локально: BRD и session report обновлены, markdown-таблиц в них нет, отдельный коммит не делался
- **Проблема**: предварительный session report был создан до запуска документатора и не соответствовал обязательному шаблону. **Решение**: тот же файл обновлён, дубль не создавался

## Результаты тестирования

- **Полный прогон до doc-only финальных правок** — `961 passed, 1 skipped, 3 warnings`
- **Targeted pytest после финальных правок** — `51 passed`
- **JSON-артефакты** — валидны
- **Финальная локальная проверка спецификаций** — критичные cross-spec расхождения не найдены
