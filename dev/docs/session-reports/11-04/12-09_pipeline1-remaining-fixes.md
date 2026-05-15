# Сессия 11-04: завершение Pipeline 1 из root-cause отчёта multi-user isolation

## Резюме

Доделаны 2 оставшиеся задачи Pipeline 1 из root-cause отчёта `11-04_09-11_multi-user-state-isolation.md`: runtime-guard в config.py и очистка фантомных `_new_*` записей в daily_session_registry. Pipeline 1 теперь полностью завершён (3/3 задачи).

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `src/claude_manager/config.py` | изменён | Добавлен runtime-guard в `load_config()`: если в `ALLOWED_USER_IDS` больше одного ID — `logger.warning` с текстом о том, что бот однопользовательский и несколько ID могут вызвать дублирование сообщений и конфликты состояния |
| `tests/test_config.py` | изменён | Добавлен класс `TestMultipleUserIdsWarning` с 3 тестами: один ID — нет warning; два ID — warning с проверкой текста; три ID — warning с правильным числом |
| `src/claude_manager/daily_session_registry.py` | изменён | Добавлена функция `_remove_phantom_entries()` — удаляет записи с session_id начинающимся на `_new_` из всех дней реестра. Вызывается в `load_registry()` после загрузки данных, перед `_ensure_today_registry()`. Не сохраняет на диск — только чистит в памяти |
| `tests/test_daily_session_registry.py` | изменён | Добавлен класс `TestRemovePhantomEntries` с 6 тестами: удаление фантомов, отсутствие фантомов, счётчик по нескольким дням, сохранность нормальных записей, интеграционный тест загрузки, пустой реестр |
| `dev/docs/logs/root-cause-reports/11-04_09-11_multi-user-state-isolation.md` | изменён | Отмечены как СДЕЛАНО: runtime-guard в config.py и очистка `_new_*` в daily_session_registry |

## Выполненные команды

- `python -m pytest tests/test_config.py tests/test_daily_session_registry.py -v` — 67/67 passed, проверка новых тестов
- `python -m pytest tests/ -v --ignore=tests/e2e --ignore=tests/integration` — 450/450 passed, полная регрессия юнит-тестов

## Решения

- **Runtime-guard — warning, не блокировка**. Причина: бот продолжает работать с несколькими ID (для одного человека с разных устройств), но явно предупреждает в логах о потенциальной проблеме.
- **Очистка `_new_*` только при загрузке, не при каждом обращении**. Причина: экономия I/O, изменения сохранятся на диск при следующей штатной записи (register_session, update_session_id).

## Контекст для следующей сессии

Pipeline 1 из root-cause отчёта `11-04_09-11_multi-user-state-isolation.md` полностью завершён (3/3):
- [x] asyncio.Lock в process_manager (коммит `176ffd9`, ранее)
- [x] Runtime-guard в config.py (эта сессия)
- [x] Очистка `_new_*` в daily_session_registry (эта сессия)

Следующий шаг — **Pipeline 2**: watcher ownership + E2E изоляция (архитектурное изменение). Зависит от Pipeline 1 (Lock уже стоит). Две задачи:
- Watcher: отправка уведомлений только владельцу сессии (`session_watcher.py`, `session_manager.py`)
- Отдельная переменная `E2E_TEST_USER_ID` для изоляции тестов (`.env.example`, `config.py`)

За Pipeline 2 следует Pipeline 3: документация и скиллы (CLAUDE.md, BRD, test-e2e, validate-brd, review-code).

Изменения этой сессии ещё не закоммичены.
