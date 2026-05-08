# Сессия 11-04: fix-watcher-duplicate-messages

## Резюме

Root cause analysis и исправление бага: при переключении проекта (`/pN`) watcher отправлял ВСЕ исторические сообщения из всех сессий нового проекта как «новые». Найдены две причины — пропущенный `await` и race condition. Обе исправлены, тесты обновлены и проходят (460 passed).

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `src/claude_manager/project_manager.py` | изменён | Строка 177: добавлен `await` перед `session_watcher.reset_state()` — функция стала async, но вызов остался синхронным |
| `src/claude_manager/session_watcher.py` | изменён | `reset_state()`: перестроена логика для устранения race condition — новые счётчики собираются ДО `clear()`, затем `clear()` + `update()` идут без промежуточных `await` |
| `tests/test_project_manager.py` | изменён | Тест `test_resets_all_state_modules`: мок `session_watcher.reset_state` заменён с `MagicMock()` на `AsyncMock()`, assertion — `assert_awaited_once()` |
| `tests/test_session_watcher.py` | изменён | Класс `TestResetState`: 3 существующих теста переведены на `async`/`await` с моками зависимостей (`_get_sessions_to_monitor`, `session_reader.get_session_messages`). Добавлен новый тест `test_reset_initializes_counts_for_new_project` |

## Решения

- **Атомарная подмена счётчиков вместо clear-then-init**: собираем `new_counts` dict через await-вызовы, затем делаем `_seen_message_counts.clear()` + `_seen_message_counts.update(new_counts)` без await между ними. **Причина**: между `clear()` и завершением инициализации event loop мог запустить `_poll_sessions`, которая увидела бы пустой словарь и отправила все сообщения заново.

## Проблемы и решения

- **Проблема**: `session_watcher.reset_state()` была переделана из `def` в `async def` (чтобы инициализировать счётчики нового проекта), но вызов в `project_manager.py:177` остался без `await`. Python не бросает ошибку — тихо создаёт корутину и выбрасывает, показывая только `RuntimeWarning` в логах. **Решение**: добавлен `await`.
- **Проблема**: race condition — между `_seen_message_counts.clear()` и завершением цикла инициализации (где есть `await`) event loop мог переключиться на `_poll_sessions`. **Решение**: сначала собираем новые данные, потом атомарно подменяем (clear + update без await между ними).
- **Проблема**: тест `test_main_logs_working_directory` падает при полном прогоне, но проходит в изоляции. **Решение**: не связан с текущими изменениями — это pre-existing flaky-тест из-за утечки глобального состояния `config.WORKING_DIR` между тестами.

## Контекст для следующей сессии

- Исправления в `project_manager.py` и `session_watcher.py` **не закоммичены** — пользователь не просил коммит
- Также в git status есть незакоммиченные изменения в `tests/e2e/test_session_flow.py` (маркеры `✅` в `wait_for_matching_response`) — это изменения из предыдущих сессий
- Flaky-тест `test_main.py::TestMain::test_main_logs_working_directory` — нужно отдельно разобраться с утечкой `config.WORKING_DIR` между тестами
- В untracked files есть документы из предыдущих сессий: `dev/docs/logs/bugfix/`, два session-report'а за 10-04
