# Сессия 13-05: codex-sessions-preview-bugfix

## Коротко

Исправлен баг Telegram-команды `/sessions`: для Codex-сессий в проекте `reviews-analyzer` список показывал служебный блок `AGENTS.md instructions` вместо первого реального сообщения пользователя.

Причина была в том, что Codex записывает bootstrap-инструкции проекта в rollout-файл как `response_item` с ролью `user`, а код preview выбирал первый `user`-блок без фильтрации.

## Рабочие файлы

- **`src/claude_manager/codex_session_file_listing.py`** — изменён фильтр preview для Codex rollout-файлов. Добавлены маркеры `CODEX_BOOTSTRAP_AGENTS_PREFIX` и `CODEX_BOOTSTRAP_INSTRUCTIONS_MARKER`; `_read_first_user_response_item_blocking()` теперь пропускает injected `AGENTS.md instructions` и продолжает поиск реального пользовательского сообщения.
- **`tests/test_codex_backend.py`** — добавлен регрессионный тест `test_list_session_files_skips_codex_bootstrap_user_message`. Тест создаёт rollout-файл с bootstrap user message перед реальным user message и проверяет, что preview берётся из реального сообщения.
- **`dev/docs/logs/bugfix/13.05_07.49-universal-bug-fixer-codex-sessions-preview/report.md`** — сохранён bugfix-отчёт с evidence, root cause, fix и verification.
- **`dev/docs/session-reports/13-05/10-29_codex-sessions-preview-bugfix.md`** — этот handoff-отчёт для продолжения работы в новой сессии.
- **`dev/docs/docs-index.md`** — обновляется, чтобы новая папка session reports за `13-05` была видна в индексе документации.

## Решения

- Preview Codex-сессии фильтрует только специфический bootstrap-блок, который начинается с `# AGENTS.md instructions for ` и содержит `<INSTRUCTIONS>`.
- Фильтр оставлен узким, чтобы не выкидывать обычные пользовательские сообщения про `AGENTS.md`.
- Чтение snapshot/messages для watcher не менялось: пользовательский баг был только в preview команды `/sessions`, а watcher доставляет только assistant-сообщения.
- Бот не перезапускался из текущего процесса. В `CLAUDE.md` проекта рестарт через `restart-claude-manager.sh` запрещён из подпроцесса бота.

## Проверки

- `head ... | jq ...` на живом rollout-файле Codex подтвердил порядок записей: `developer`, затем bootstrap `user` с `AGENTS.md instructions`, затем реальное `user`-сообщение.
- `.venv/bin/python -m pytest tests/test_codex_backend.py::test_list_session_files_skips_codex_bootstrap_user_message -q` падал до фикса с preview из `AGENTS.md instructions`.
- `.venv/bin/python -m pytest tests/test_codex_backend.py::test_list_session_files_skips_codex_bootstrap_user_message -q` прошёл после фикса.
- `.venv/bin/python -m pytest tests/test_codex_backend.py -q` прошёл: 21 тест.
- `.venv/bin/python -m pytest tests/test_bot.py::TestHandleSessions -q` прошёл: 3 теста.
- `.venv/bin/python -m pytest tests/ -q` прошёл: 966 тестов, 1 skipped, 3 warnings из `telegram.error.PTBDeprecationWarning`.
- Live-check через `CodexBackend().list_session_files_for_project('/Users/ivan/Desktop/claude-sandbox/reviews-analyzer')` больше не возвращает `AGENTS.md instructions` как первые preview.

## Риски и ограничения

- Запущенный Telegram-бот увидит изменение только после перезапуска процесса.
- `dev/docs/logs/bugfix/.../report.md` отмечает, что строгий `universal-bug-fixer` pre-flight ожидал `architecture.md` в корне `claude_manager`, но этого файла нет; архитектурный контекст был взят из `CLAUDE.md`.
- Предупреждения полного тестового прогона идут из сторонней библиотеки `python-telegram-bot` и не связаны с изменением preview.

## Продолжение

- Сделать коммит с кодом, тестом, bugfix-отчётом, session report и обновлением `docs-index.md`.
- После безопасного внешнего перезапуска бота проверить `/sessions` в Telegram на проекте `reviews-analyzer`.
