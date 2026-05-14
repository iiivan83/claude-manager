# Сессионный отчёт: all-projects monitoring

## Коротко

В этой сессии продолжена и стабилизирована реализация режима `/all`: бот умеет показывать сообщения из всех проектов, давать кликабельные команды вида `/3s12`, блокировать отправку агенту из общего режима и сохранять обычную доставку непрочитанных сообщений при входе в конкретный проект.

Дополнительно закрыт крайний случай: если пользователь выходит из `/all` через `/pN` или `/2s9`, но переключение проекта падает, бот возвращает пользователя обратно в общий мониторинг, а не оставляет его в промежуточном состоянии без нормального watcher.

## Рабочие файлы

- **`docs/superpowers/specs/2026-05-14-all-projects-monitoring-design.md`** — дизайн режима `/all`: что должен видеть пользователь, как работают кликабельные команды и почему all-mode не должен помечать сообщения прочитанными в исходном проекте.
- **`docs/superpowers/plans/2026-05-14-all-projects-monitoring-implementation.md`** — план реализации по шагам. На момент отчёта файл остаётся незакоммиченным в рабочем дереве.
- **`src/claude_manager/all_projects_monitor.py`** — новый фоновый наблюдатель по всем проектам. Он хранит свои cursor-счётчики отдельно от `session_watcher`, чтобы просмотр в `/all` не портил обычную доставку непрочитанных сообщений.
- **`src/claude_manager/project_manager.py`** — добавлена публичная обёртка `collect_pending_messages_for_project`, которая собирает pending-сообщения для уже активного проекта при выходе из `/all`.
- **`src/claude_manager/bot.py`** — подключены `/all`, строка `/all all` в `/projects`, блокировки ввода в all-mode, обработчик `/2s9` и восстановление all-mode после неудачного переключения.
- **`tests/test_all_projects_monitor.py`** — unit-тесты для нового глобального watcher.
- **`tests/test_bot.py`** — unit-тесты Telegram-обработчиков: `/all`, `/projects`, `/pN`, `/2s9`, блокировки текста, фото, документов и `/new`.
- **`tests/test_project_manager.py`** — unit-тест публичной pending-обёртки.

## Решения

- Для `/all` используется отдельный модуль `all_projects_monitor`, а не расширение обычного `session_watcher`. Это сохраняет главное правило: сообщение, показанное в общем режиме, остаётся непрочитанным для исходного проекта и будет доставлено при входе в него.
- Команда в all-mode имеет вид `/<project_number>s<session_number>`, например `/3s12`. Она кликабельна в Telegram и резолвится через link registry в точный проект, session_id и backend.
- В all-mode запрещены обычный текст, фото, документы и `/new`. Пользователь должен сначала войти в конкретный проект и сессию.
- При успешном выходе из `/all` normal watcher возобновляется только если больше нет чатов в all-mode.
- При неудачном переключении проекта из `/all` бот снова включает `all_projects_monitor.enable_for_chat(chat_id)` и не делает `session_watcher.resume_all()` поверх него. Это защищает от состояния, где all-mode уже выключен, а рабочий проект так и не выбран.

## Коммиты

- **`081deb2 docs: add all projects monitoring design`** — дизайн режима `/all`.
- **`793b6b5 feat: add all projects monitor core`** — ядро `all_projects_monitor` и тесты.
- **`a7b402c feat: expose active project pending collection`** — публичная pending-обёртка в `project_manager`.
- **`8e60de8 feat: wire all projects monitoring into bot`** — интеграция `/all` в Telegram-бот.
- **`633f196 fix: restore all mode after failed project switch`** — восстановление all-mode после неудачного `/pN` или `/2s9`.

## Проверки

- **Точечная проверка нового фикса:**
  `.venv/bin/python -m pytest tests/test_bot.py::TestHandleSwitchProject::test_failed_switch_restores_all_projects_mode tests/test_bot.py::TestHandleSwitchProjectSession::test_failed_link_switch_restores_all_projects_mode -q`
  Результат: `2 passed`.

- **Проверка всего `tests/test_bot.py`:**
  `.venv/bin/python -m pytest tests/test_bot.py -q`
  Результат: `97 passed`.

- **Целевой регрессионный набор по `/all`:**
  `.venv/bin/python -m pytest tests/test_all_projects_monitor.py tests/test_bot.py tests/test_project_manager.py tests/test_unread_buffer.py tests/test_session_watcher.py -q`
  Результат: `163 passed`.

- **Полный набор без внешнего Claude CLI контракта:**
  `.venv/bin/python -m pytest tests/ -q --deselect tests/integration/test_claude_cli_contract.py::test_claude_backend_stream_json_and_session_file_contract`
  Результат: `987 passed, 1 skipped, 1 deselected`.

- **Полный набор целиком:**
  `.venv/bin/python -m pytest tests/ -q`
  Результат: `1 failed, 987 passed, 1 skipped`. Единственное падение: `tests/integration/test_claude_cli_contract.py::test_claude_backend_stream_json_and_session_file_contract`, который запускает реальный `/Users/ivan/.npm-global/bin/claude`. Это внешний контрактный тест локального Claude CLI, не код `/all`.

## Риски и ограничения

- Живой рестарт Telegram-бота не выполнялся. По правилам проекта это отдельный smoke test через `./restart-claude-manager.sh`, и его нужно запускать только после явного разрешения пользователя.
- Полный pytest без исключений остаётся заблокирован состоянием реального Claude CLI. Для проверки кода проекта использован полный набор с `--deselect` одного внешнего контракта.
- В рабочем дереве до этого отчёта уже были незакоммиченные `.agents` и docs-файлы. Они не относятся к фиксу `/all` и не должны попадать в коммит отчёта без отдельного решения.
- Рабочая папка не является отдельным git worktree: `GIT_DIR == GIT_COMMON`. Новые правки выполнялись поверх текущей ветки `codex-support-spec-implementation-cycle`, потому что предыдущие коммиты фичи уже были сделаны в этой рабочей копии.

## Продолжение

1. При необходимости выполнить live smoke test: запустить `./restart-claude-manager.sh`, затем в Telegram проверить `/projects`, `/all`, блокировку текста в all-mode и переход по `/3s12`.
2. Если live smoke проходит, решить судьбу незакоммиченного плана `docs/superpowers/plans/2026-05-14-all-projects-monitoring-implementation.md`: либо закоммитить отдельным docs-коммитом, либо оставить как рабочий артефакт.
3. Разобраться отдельно с внешним контрактным тестом Claude CLI: проверить авторизацию и поведение `/Users/ivan/.npm-global/bin/claude` в stream-json режиме.
4. Не трогать `.claude/**` и generated `.agents/**` вручную; зеркала Codex-скиллов должны обновляться только через sync tooling проекта.
