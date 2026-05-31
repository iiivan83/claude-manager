# Handoff: спецификация recent_sessions v1

## Коротко

Создана и проверена спецификация v1 для `recent_sessions` — небольшой постоянной таблицы
последних сессий. Она нужна, чтобы `/sessions` и `/all` брали быстрый список кандидатов из
индекса, а не запускали повторный полный обход истории Codex-сессий.

Практический смысл: следующая сессия может сразу переходить к плану реализации и тестам, не
возвращаясь к выбору архитектуры.

## Рабочие файлы

- `dev/docs/specs/31.05_19.43-recent-sessions-v1-spec.md` — финальная спецификация для
  реализации.
- `dev/docs/logs/spec-creator/31.05_19.43-spec-creator-recent-sessions-v1/` — полный лог
  пайплайна spec-creator и JSON-артефакты агентов.
- `dev/docs/logs/spec-creator/31.05_19.43-spec-creator-recent-sessions-v1/orchestrator-log.json`
  — журнал этапов пайплайна; `spec_path` указывает на финальную спецификацию.
- `dev/docs/logs/spec-creator/31.05_19.43-spec-creator-recent-sessions-v1/agent-outputs/06-final-requirements-checker.json`
  — финальная проверка: `24 passed`, `0 failed`, verdict `pass`.

## Решения

- v1 остаётся простым storage/refresh решением, не event-driven watcher-архитектурой.
- `recent_sessions` хранит максимум 30 последних сессий на `project_path` across all backends.
- `/sessions` показывает максимум 15 строк из сохранённых 30 и сохраняет plain text команды
  `/1`, `/2`, `/3`.
- `/all` получает отдельный default global cap 80, чтобы poll каждые 2 секунды не рос от числа
  всех проектов без ограничения.
- Cursor/read state нельзя хранить только в удаляемых строках `recent_sessions`; нужен
  независимый `session_cursor_state` или доказанный совместимый state-store.
- Полный текст сообщений остаётся в session files. Таблица хранит metadata и preview cache.
- SQLite допустим только через неблокирующий async facade: `asyncio.to_thread` или worker,
  write lock, short transactions и `busy_timeout`.
- Реализация должна держать storage и refresh в новых небольших модулях. Большие текущие файлы
  получают только тонкую интеграцию.

## Проверки

- JSON-артефакты всех этапов проверены через `.venv/bin/python -m json.tool`.
- В `spec-draft.md` и финальной спецификации нет markdown-таблиц.
- Ревью спецификации: `0 critical`, `0 major`, `1 minor`.
- Minor R1 применён: спецификация теперь явно требует считать top-level публичные функции и
  оценивать риск god-module при значении больше 10.
- Финальная проверка Stage 5: `24 passed`, `0 failed`, `1 not_applicable`, verdict `pass`.
- Продуктовый код не менялся, поэтому pytest не запускался.

## Риски и ограничения

- Реализация должна доказать отсутствие повторного historical scan не таймингами, а
  spy/counter-тестами на запрещённые вызовы.
- Нельзя добавлять storage-логику в `all_projects_monitor.py`,
  `telegram_session_handlers.py`, `codex_session_file_listing.py`, `session_reader.py`,
  `daily_session_registry.py` или `coding_agent_backend.py`.
- Особенно опасен `codex_session_file_listing.py`: на момент спецификации он около 499 строк,
  то есть один шаг до 500-line техдолга.
- При правке любых code modules нужно считать строки и top-level публичные функции по правилу
  AGENTS.md.

## Продолжение

1. Написать план реализации по финальной спецификации.
2. Начать с storage unit tests: schema init, upsert, stable sort, cap 30 across backends,
   cursor-state survives pruning.
3. Затем добавить safety-refresh tests с forbidden-scan assertions для Codex.
4. После этого внедрять тонкую интеграцию в `/sessions` и `/all`, сохраняя текущую
   daily-session и pending-семантику.
