# Сессионный отчёт: handoff по медленному переключению проектов

## Коротко

В этой сессии разобрали, почему переключение между проектами в Telegram-боте Claude Manager занимает 7-17 секунд. Причина не в Telegram и не в самом сообщении `/pN`: бот перед ответом повторно сканирует тысячи недавних Codex rollout-файлов, чтобы понять, какие сессии относятся к выбранному проекту.

Главный вывод: нужно не “ещё сильнее параллелить чтение”, а убрать повторный глобальный поиск. Следующая сессия должна внедрить быстрый индекс Codex-сессий на 4 дня и подключить его к горячему пути переключения проекта.

## Рабочие файлы

- `dev/docs/logs/root-cause-reports/30-05_18-32_slow-project-switch.md` — полный RCA-отчёт: цепочка причин, замеры, рекомендации и чек-лист исправлений.
- `dev/docs/logs/root-cause-analysis/30.05_18.26-root-cause-analysis-slow-project-switch/orchestrator-log.json` — лог RCA-пайплайна по этой проблеме.
- `src/claude_manager/project_manager.py` — координатор переключения проектов. Важные места: `switch_project`, `_perform_switch`, `_collect_pending_messages`, `_collect_pending_for_backend`.
- `src/claude_manager/session_watcher.py` — фасад watcher-а. Сейчас `reset_state()` проходит backend-и последовательно.
- `src/claude_manager/coding_agent_session_file_poller.py` — реальная per-backend логика watcher-а. Важные места: `reset_state`, `_get_sessions_to_monitor`, `_read_baseline_states_concurrently`.
- `src/claude_manager/codex_session_file_listing.py` — поиск Codex rollout-файлов. Именно здесь сейчас каждый вызов открывает все rollout-файлы за lookback-окно и проверяет `payload.cwd`.
- `src/claude_manager/unread_buffer.py` — in-memory snapshot непрочитанных. Сейчас snapshot хранит `raw_record_count` и `last_delivered_idx`, но не хранит `last_modified_at`.
- `dev/docs/specs/realised/project_manager_spec.md` — спецификация переключения проектов. Её нужно обновить после фикса.
- `dev/docs/adr/28.05_22.29-session-change-documenter-operational-session-listing-lookback.md` и `dev/docs/adr/29.05_00.25-session-change-documenter-poll-once-operational-lookback.md` — предыдущие решения по lookback. Их нужно читать перед изменением логики.

## Решения

- Иван отклонил идею отвечать “переключено” до проверки pending. Значит следующий фикс не должен менять UX в эту сторону: подтверждение переключения остаётся после штатной проверки.
- Lookback для индекса Codex-сессий выбран 4 дня.
- Индекс не должен расти бесконечно. Правильная модель: скользящее окно последних 4 дней, где старые дни выпадают при пересборке.
- Индекс должен быть картой “где лежит файл”, а не источником содержимого. Preview, summary и отчёты по сессиям должны читать реальные файлы или `daily_sessions.json`.
- Список должен обновляться безопасно:
  - если changed today/yesterday/etc directories — пересобрать индекс;
  - если известен конкретный session_id — обновить точечно;
  - для уже найденных файлов проверять `mtime`, чтобы понять, изменился ли файл;
  - TTL использовать только как страховку, а не как единственный механизм актуализации.

## Проверки

- Живые логи бота за 30-05 подтвердили задержки переключения: 7, 10, 17, 9 и 7 секунд.
- Подсчёт Codex-файлов показал: всего 14063 rollout-файлов, за 30-05 — 1977, за 29-05 — 1929. Текущее 2-дневное окно уже содержит 3906 файлов.
- Локальный замер `list_all_session_files_for_project(..., lookback_days=2)` показал, что Codex listing даже для проектов с 0 найденных сессий занимает примерно 2.3-3.3 секунды.
- Локальный замер pending-сбора показал, что `_collect_pending_messages` занимает 2.96-3.92 секунды даже при 0 pending.
- RCA-отчёт проверен:
  - файл существует;
  - JSON-лог RCA валиден через `python3 -m json.tool`;
  - `git diff --check` по отчёту и логу без ошибок;
  - markdown-таблиц в отчёте нет.

## Риски и ограничения

- Код ещё не менялся. Это handoff после расследования и обсуждения решения.
- Нельзя делать раннее подтверждение переключения до pending-проверки: Иван явно сказал, что пункт 3 “точно не надо делать”.
- При реализации нужно помнить правило проекта про размер файлов. Уже сейчас:
  - `project_manager.py` — 597 строк, выше порога 500;
  - `coding_agent_session_file_poller.py` — 653 строки, превышение уже задокументировано;
  - `codex_session_file_listing.py` — 494 строки, почти у порога 500;
  - `daily_session_registry.py` — 628 строк и больше 10 публичных функций;
  - `session_manager.py` — 470 строк и больше 10 публичных функций.
- Поэтому следующий кодовый проход не должен просто дописывать крупные блоки в существующие god-модули. Новый индекс лучше выделить в отдельный модуль, например `codex_session_index.py`.
- Секреты в отчёт не добавлялись. `.env` не читался и не цитировался.
- В рабочем дереве уже были чужие/unrelated untracked-артефакты до создания этого handoff. Новые артефакты этой сессии: RCA-лог, RCA-отчёт и этот сессионный отчёт.

## Продолжение

1. Прочитать RCA-отчёт `dev/docs/logs/root-cause-reports/30-05_18-32_slow-project-switch.md`.
2. Начать с TDD:
   - индекс хранит только последние 4 дня;
   - старые дни выпадают при пересборке;
   - несколько вызовов подряд не запускают повторный global scan;
   - появление нового rollout-файла инвалидирует или обновляет индекс;
   - known `session_id` можно обновить точечно;
   - pending no-op не читает полный snapshot, если файл не менялся.
3. Реализовать отдельный модуль индекса Codex-сессий, не раздувая `codex_session_file_listing.py`.
4. Подключить индекс к `CodexBackend.list_all_session_files_for_project`.
5. Подключить тот же быстрый источник к `session_watcher.reset_state` и `project_manager._collect_pending_messages`, не меняя порядок ответа пользователю.
6. Расширить pending snapshot: добавить `last_modified_at` или другой дешёвый признак изменения файла.
7. Обновить документацию:
   - `dev/docs/specs/realised/project_manager_spec.md`;
   - Codex backend spec;
   - при необходимости ADR про Codex operational session index.
8. Проверить целевыми тестами и живым замером. Цель: переключение проекта около 1-2 секунд вместо 7-17 секунд.
