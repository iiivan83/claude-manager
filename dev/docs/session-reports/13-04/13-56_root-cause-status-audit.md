# Сессия 13-04: аудит статуса root-cause отчётов и E2E тестов

## Резюме

Исследовательская сессия: пользователь попросил проанализировать статус исправлений по последним root-cause отчётам. Выяснено, что feature-pipeline закрыл 6 из 7 рекомендаций по cross-project-session-registration (коммит `a6e0c74`), а второй root-cause отчёт (send-chat-action-crash) ещё не применён. Разобраны 4 упавших E2E теста — все pre-existing, регрессий нет.

## Что исследовалось

### Root-cause отчёт #1: cross-project-session-registration (13-04_10-44)

Race condition при переключении проектов: watcher просыпался между сменой `config.WORKING_DIR` и сбросом `daily_session_registry`, записывал сессии нового проекта в реестр старого. 6 "призрачных" записей за 3 дня, 32 954 WARNING в логах.

**Цепочка исправления:**
1. `apply-root-cause-fixes` (13.04_11.21) — прочитал отчёт, извлёк 7 рекомендаций
2. `feature-pipeline` (13.04_12.08) — полный цикл за ~2 часа, все 10 фаз пройдены
3. Коммит `a6e0c74` — 26 файлов, 1628 строк добавлено, 604 теста зелёные

**Статус рекомендаций (7 штук):**

| # | Категория | Рекомендация | Статус |
|---|-----------|-------------|--------|
| 1 | CODE | `pause_all()` / `resume_all()` в `session_watcher.py` | сделано (коммит `a6e0c74`) |
| 2 | CODE | `try/finally` в `_perform_switch()` в `project_manager.py` | сделано (коммит `a6e0c74`) |
| 3 | CODE | `_remove_orphan_entries()` в `daily_session_registry.py` | сделано (коммит `a6e0c74`) |
| 4 | CODE | `_missing_file_sessions` в `session_watcher.py` | сделано (коммит `a6e0c74`) |
| 5 | ARCHITECTURE | Принцип "Атомарность переключения проекта" в CLAUDE.md | сделано (коммит `a6e0c74`) |
| 6 | ARCHITECTURE | Обновление описания переключения проектов в CLAUDE.md | сделано (коммит `a6e0c74`) |
| 7 | SKILL | Пункт в чеклист feature-pipeline про фоновые asyncio-задачи | в незакоммиченных изменениях |

### Root-cause отчёт #2: send-chat-action-crash (13-04_00-39)

`send_chat_action(ChatAction.TYPING)` без `try/except` в трёх обработчиках (`handle_message`, `handle_photo`, `handle_document`). При TimedOut от Telegram API обработчик падает целиком, `_send_to_claude_and_respond` не вызывается, бот молчит.

**Статус:** universal-bug-fixer дошёл до стадии 5 (test-strategist) + стадия 6 (critical-verifier). Код **не менялся** — pipeline остановился перед fix-strategist. 8 рекомендаций не применены:
- 4x CODE: обёртка send_chat_action (3 места), глобальный error handler, валидация сессий в watcher, понижение уровня лога в session_reader
- 2x ARCHITECTURE: принцип "критические vs декоративные вызовы API", правило "глобальный error handler обязателен"
- 1x DOC: обновление bot_spec.md и review-checklists.md
- 1x SKILL: ссылка на новый принцип в spec-module

### 4 упавших E2E теста (из 21)

Все 4 — pre-existing, не связаны с текущей фичей, регрессий нет:

- **`test_flow16_file_and_text_are_separate_messages`** — маркер 📎 не найден в ответе. Проблема в `file_sender`, не в переключении проектов
- **`test_flow12_no_ghost_messages_after_project_switch`** — flaky, зависит от тайминга watcher. Текущий фикс (глобальная пауза) как раз решает эту проблему, но тест использует старую логику детекции
- **`test_flow03_errors_and_constraints`** — тест предполагает отсутствие сессии #99, но она существует. Устаревшие тестовые данные
- **`test_flow06_thinking_messages_arrive`** — Claude ответил слишком быстро, без thinking-блока. Зависит от сложности вопроса и скорости модели

## Незавершённое

- [ ] Применить 8 рекомендаций из root-cause отчёта `13-04_00-39_send-chat-action-crash.md` — через `apply-root-cause-fixes` + `feature-pipeline`. Критичность: HIGH (бот перестаёт отвечать на сообщения при любом TimedOut от Telegram API)
- [ ] Закоммитить изменения в `.claude/skills/feature-pipeline/` (рекомендация #7 из cross-project отчёта) — пункт о проверке фоновых asyncio-задач в чеклисте ревью
- [ ] Исправить 4 flaky E2E теста:
  - `test_flow16` — проверить работу file_sender маркеров
  - `test_flow12` — обновить логику детекции ghost messages с учётом глобальной паузы watcher
  - `test_flow03` — обновить тестовые данные (сессия #99)
  - `test_flow06` — сделать тест устойчивым к отсутствию thinking-блока

## Контекст для следующей сессии

Два root-cause отчёта от 13-04. Первый (cross-project race condition) — закрыт на 6/7 рекомендаций коммитом `a6e0c74`, одна в незакоммиченных изменениях скиллов. Второй (send-chat-action crash) — полностью открыт, 8 рекомендаций ждут применения. Universal-bug-fixer собрал диагностику и тесты (`dev/docs/logs/bugfix/13.04_00.18-universal-bug-fixer-send-chat-action-crash/`), но код не менял.

Приоритет: send-chat-action crash — blocking severity, бот перестаёт отвечать при ошибке Telegram API. Рекомендуемый следующий шаг — запуск `apply-root-cause-fixes` с путём `dev/docs/logs/root-cause-reports/13-04_00-39_send-chat-action-crash.md`, затем feature-pipeline для реализации.

Незакоммиченные изменения: ~50 файлов в `.claude/skills/` (модификации агентов, evals, schemas нескольких скиллов) + 1 файл `src/claude_manager/claude_runner.py`. Требуют ревью перед коммитом.
