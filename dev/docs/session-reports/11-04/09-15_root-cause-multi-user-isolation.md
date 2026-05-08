# Сессия 11-04: root-cause-analysis multi-user state isolation

## Резюме

Пайплайн root-cause-analysis завершён. Найдена корневая причина нестабильности бота при подключении второго пользователя (E2E тестовый аккаунт Telethon): архитектура строго однопользовательская, но `ALLOWED_USER_IDS` принимает несколько ID без runtime-защиты. Сформирован отчёт с 5 рекомендациями по исправлению кода и 7 рекомендациями по предотвращению.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `dev/docs/logs/root-cause-reports/11-04_09-11_multi-user-state-isolation.md` | создан | Root-cause отчёт: полная цепочка причин (5 звеньев), анализ 6 скиллов и 5 документов, 5 рекомендаций по исправлению, 7 рекомендаций по предотвращению, верификация всех решений |

## Решения

- **Корневая причина — отсутствие архитектурного инварианта «один пользователь»**. BRD декларирует single-user, но ограничение не закреплено ни в коде (нет runtime guard), ни в CLAUDE.md, ни в спецификациях модулей. E2E тесты добавляют второй user ID в белый список, создавая неподдерживаемый multi-user сценарий.

- **Watcher broadcast — ключевая точка дублирования**. `session_watcher.py:174` итерирует по ВСЕМ `config.ALLOWED_USER_IDS` и шлёт каждому каждое сообщение. Нет понятия «владелец сессии». Причина: `for chat_id in config.ALLOWED_USER_IDS` в `_notify_chats()`.

- **Race condition в process_manager существует независимо от multi-user**. `_busy_flags` индексируются по `session_id` без `asyncio.Lock`. При `concurrent_updates=256` два запроса могут одновременно пройти проверку занятости.

- **Приоритет исправлений:** (1) runtime-guard в config.py, (2) E2E изоляция через `E2E_TEST_USER_ID`, (3) asyncio.Lock в process_manager, (4) watcher отправка только владельцу сессии.

## Незавершённое

- [ ] Применить рекомендации из root-cause отчёта — запустить скилл `apply-root-cause-fixes` с путём `dev/docs/logs/root-cause-reports/11-04_09-11_multi-user-state-isolation.md`
- [ ] 5 CODE-исправлений: runtime-guard в config.py, watcher broadcast fix, asyncio.Lock в process_manager, E2E_TEST_USER_ID, очистка фантомных _new_* записей
- [ ] 2 ARCHITECTURE-изменения: добавить «Однопользовательский инвариант» и «E2E тестирование и изоляция» в CLAUDE.md
- [ ] 1 DOC-изменение: предупреждение в BRD про ALLOWED_USER_IDS
- [ ] 3 SKILL-обновления: test-e2e, validate-brd, review-code — ссылки на новый инвариант
- [ ] Массовые изменения в `.claude/skills/` (40+ файлов) — не связаны с root-cause; это обновления из предыдущей сессии по пропагации бюджетов и structural-validator

## Контекст для следующей сессии

Root-cause отчёт готов и верифицирован: `dev/docs/logs/root-cause-reports/11-04_09-11_multi-user-state-isolation.md`. Все 12 рекомендаций (5 CODE + 2 ARCHITECTURE + 1 DOC + 3 SKILL + 1 cleanup) одобрены верификатором.

Следующий шаг — запустить `apply-root-cause-fixes` для применения рекомендаций. Чек-лист исправлений находится в конце отчёта.

В git 40+ изменённых файлов скиллов (`.claude/skills/`) — это результат предыдущей сессии по пропагации бюджетов через `cli-budgets.env` и добавлению structural-validator. Эти изменения не связаны с текущей root-cause проблемой.
