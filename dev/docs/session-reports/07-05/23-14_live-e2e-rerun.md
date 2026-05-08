# Сессия 07-05: live-e2e-rerun

## Резюме

Перезапущен живой Telegram-бот и прогнаны реальные E2E-сценарии через Telethon. Бот работает и отвечает, старый сценарный runner прошёл без технического падения, основной pytest E2E suite без Codex-support файла завершился красным: 14 passed, 10 failed.

## Изменённые файлы

- **`.venv`** — локально восстановлена как symlink на `/Users/ivan/.venvs/claude-manager`.
  - Причина: `restart-claude-manager.sh` не проходил preflight, потому что прежняя `.venv` была неполной папкой без `bin/python`.
  - Старая неполная папка сохранена как `.venv.missing-bin-backup-20260507-223057/`.
  - Изменение не предназначено для коммита; `.venv` и backup добавлены в локальный `.git/info/exclude`.

- **`.env`** — временно изменялся и затем восстановлен из backup.
  - Временно добавлялся `E2E_TEST_USER_ID`, чтобы изолировать Telethon-чат от watcher-рассылок из чужих сессий.
  - После тестов `.env` восстановлен из резервной копии, временный backup удалён.

- **`~/.claude-manager-silence-mode`** — временно выключался и затем включён обратно.
  - Перед E2E был `enabled=true`.
  - Для проверки thinking-сообщений временно выполнено `Silence off`.
  - После тестов выполнено `Silence on`, финальное состояние снова `{"enabled": true}`.

- **`.git/info/exclude`** — локально добавлены исключения `.venv` и `.venv.missing-bin-backup-*/`.
  - Причина: `.gitignore` игнорирует `.venv/` как папку, но после восстановления `.venv` стала symlink и попала в `git status`.
  - Это локальный git-файл, не попадает в коммит.

## Выполненные команды

- `./restart-claude-manager.sh` — безопасный рестарт бота через launchd с preflight и post-flight.
- `~/.venvs/claude-manager/bin/python tests/e2e/run_e2e_tests.py` — старый последовательный E2E-runner через настоящий Telegram.
- `~/.venvs/claude-manager/bin/python -m pytest tests/e2e --ignore=tests/e2e/test_agent_backend_selection.py --collect-only -q` — проверка, что из pytest E2E исключён только новый Codex-support файл.
- `~/.venvs/claude-manager/bin/python -m pytest tests/e2e --ignore=tests/e2e/test_agent_backend_selection.py -q --tb=short` — полный живой pytest E2E-прогон без новых сценариев `/agent`.
- Telethon helper snippets — отправка `/projects`, `/p5`, `Silence off`, `Silence on`, проверка авторизации и E2E user id.
- `launchctl setenv E2E_TEST_USER_ID ...` / `launchctl unsetenv E2E_TEST_USER_ID` — попытка временной E2E-изоляции через launchd environment; не попала в уже загруженный LaunchAgent, затем убрана.

## Результаты тестов

- **`tests/e2e/run_e2e_tests.py`** — завершился с exit code 0.
  - Выполнил 12 живых сценарных шагов.
  - 2 сценария помечены `PASS`: `FIX-01` thinking-сообщения, `FIX-02` busy-сессия.
  - 10 базовых CJM оставлены `PENDING`, потому что старый runner записывает ответы, но не выставляет строгий verdict для этих шагов.

- **`pytest tests/e2e --ignore=tests/e2e/test_agent_backend_selection.py`**, первый прогон — 14 passed, 10 failed за 12:49.
  - Часть падений была загрязнена watcher-сообщениями из текущей Codex-сессии.
  - Причина загрязнения: в `.env` отсутствовал `E2E_TEST_USER_ID`, а Telethon user был одновременно в `ALLOWED_USER_IDS`.

- **`pytest tests/e2e --ignore=tests/e2e/test_agent_backend_selection.py`**, повтор после временного `E2E_TEST_USER_ID` в `.env` — 14 passed, 10 failed за 13:12.
  - Повтор подтвердил, что итоговый красный статус не только из-за watcher-шума.
  - Основные причины падений: старые E2E-ожидания не учитывают backend-aware заголовок `#N 🤖 Claude ✅`; часть сценариев жёстко ждёт конкретный короткий ответ от живого Claude.

## Решения

- **Решение:** исключить только `tests/e2e/test_agent_backend_selection.py` из полного E2E pytest-прогона. **Причина:** пользователь уточнил, что новые Codex-support сценарии были написаны до запуска доработки и сейчас ожидаемо могут не работать.
- **Решение:** временно выключить silence mode перед E2E. **Причина:** старые реальные сценарии проверяют доставку промежуточных `⏳` сообщений.
- **Решение:** перед прогоном переключить бота на проект `claude_manager` через `/projects` и `/p5`. **Причина:** после рестарта бот восстановил последний проект `sushkof-budget-analyzer`, а E2E-сценарии проверяют поведение в `claude_manager`.
- **Решение:** не считать `restart-claude-manager.sh` exit code 1 окончательным доказательством падения бота. **Причина:** post-flight читает stale launchctl status `-15`, но фактическая проверка процессов и логов показывает живой новый Python-процесс бота.

## Проблемы и решения

- **Проблема:** первый `./restart-claude-manager.sh` упал на preflight: `Python не найден: .../.venv/bin/python`.
  **Решение:** обнаружено, что `.venv` была неполной папкой без `bin/`; старая папка переименована в backup, `.venv` восстановлена symlink-ом на `/Users/ivan/.venvs/claude-manager`.

- **Проблема:** launchd post-flight возвращает `exit code = -15`, хотя бот фактически стартует.
  **Решение:** состояние проверялось по `pgrep`, `launchctl list`, `tail ~/Library/Logs/claude-manager.log`; бот был жив и обрабатывал Telegram-команды. Проблема post-flight осталась как отдельный технический долг.

- **Проблема:** первый pytest E2E был загрязнён watcher-сообщениями из текущей Codex-сессии.
  **Решение:** сначала пробовался `launchctl setenv E2E_TEST_USER_ID`, но переменная не попала в уже загруженный LaunchAgent; затем `E2E_TEST_USER_ID` временно добавлен в `.env`, бот перезапущен, повторный прогон выполнен в чистом режиме.

- **Проблема:** повторный чистый pytest E2E всё равно дал 10 failed.
  **Решение:** зафиксировано, что это устойчивые падения старых сценариев: они ищут подстроки вида `#N ✅`, но текущий backend-aware бот отвечает `#N 🤖 Claude ✅`; также есть brittle-ожидания от живого Claude (`4`, `ок`, `как Кактус` и т.п.).

## Незавершённое

- [ ] Обновить старые E2E-тесты под backend-aware заголовки: искать `#N` и `✅` с допуском `🤖 Claude` / `⚡ Codex` между ними.
- [ ] Стабилизировать E2E-сценарии, которые требуют конкретных ответов от живого Claude; заменить жёсткую строку на semantic/regex-проверку или более детерминированный prompt.
- [ ] Исправить `restart-claude-manager.sh` post-flight, который видит stale `launchctl` status `-15` при фактически живом процессе.
- [ ] Решить конфигурацию E2E-изоляции: `E2E_TEST_USER_ID` должен быть настроен постоянно и не должен одновременно находиться в `ALLOWED_USER_IDS`, иначе watcher может загрязнять тестовый чат.
- [ ] После обновления старых E2E повторить полный прогон без `test_agent_backend_selection.py`, затем отдельно вернуться к новым Codex-support сценариям.

## Контекст для следующей сессии

Бот оставлен запущенным на проекте `claude_manager`. Silence mode восстановлен в `enabled=true`. `.env` восстановлен без временного `E2E_TEST_USER_ID`; `launchctl E2E_TEST_USER_ID` unset. `.venv` теперь symlink на рабочий venv; старая неполная папка сохранена локально как `.venv.missing-bin-backup-20260507-223057/` и скрыта через `.git/info/exclude`.

Новые Codex-support E2E сценарии не запускались в обязательном прогоне по указанию пользователя. Старый живой pytest E2E suite без `tests/e2e/test_agent_backend_selection.py` стабильно показывает 14 passed / 10 failed. Перед утверждением готовности нужно сначала обновить старые E2E-ожидания под backend-aware формат ответа.
