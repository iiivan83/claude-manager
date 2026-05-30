# Сессия 28-05: миграция claude-manager с macOS на Linux

## Резюме

Полностью убрана macOS-инфраструктура из проекта `claude-manager` и заменена на Linux/systemd-аналоги. Шесть последовательных коммитов в ветке `codex-support-spec-implementation-cycle` закрывают пять фаз спецификации `28.05_17.25-linux-bot-commands-cleanup-spec.md`. Все 1012 автотестов проходят (единственное падение — унаследованный baseline-баг, не относится к этой работе). Документалист сессии создал ADR, CLAUDE.md update log и обновил docs-index.

## Изменённые файлы

**Phase 1 — `PROJECTS_ROOT_DIR` обязательная (коммиты `6594145`, `396052b`):**

- **`src/claude_manager/config.py`** — изменён — удалена константа `DEFAULT_PROJECTS_ROOT` (macOS-путь `/Users/ivan/Desktop/claude-sandbox`), функция `_resolve_projects_root` теперь поднимает `ConfigError` при пустом значении.
- **`tests/test_config.py`** — изменён — удалены тесты на fallback на default-путь, добавлены `test_none_raises_error` и `test_empty_raises_error`, в `_make_env` добавлен дефолт `PROJECTS_ROOT_DIR` чтобы остальные тесты не падали на новой обязательности.

**Phase 2 — XDG-путь для логов (коммит `ab08cbc`):**

- **`src/claude_manager/main.py`** — изменён — `MAIN_LOG_PATH` переехал с `~/Library/Logs/claude-manager.log` на `~/.local/state/claude-manager/claude-manager.log` (XDG-стандарт).

**Phase 3 — `/restart` через systemctl (коммит `fb4846e`):**

- **`src/claude_manager/bot.py`** — изменён — `handle_restart` переписан под `systemctl --user restart claude-manager.service`, удалена константа `LAUNCHD_SERVICE_LABEL`, `RESTART_DELAY_BEFORE_KICKSTART_SECONDS` переименована в `RESTART_DELAY_BEFORE_SYSTEMCTL_SECONDS`, убран мёртвый `import os` (нужен был только для `os.getuid()` в launchctl-команде).
- **`tests/test_bot.py`** — изменён — добавлен класс `TestHandleRestart` с двумя тестами (warning message + детач-флаги subprocess `start_new_session=True`, stdout/stderr=DEVNULL).

**Phase 4 — shell-скрипты под Linux (коммит `8ebe4cd`):**

- **`restart-claude-manager.sh`** — переписан целиком — хелперы `check_editable_install`, `service_is_running`, `print_diagnostics_on_failure` вынесены для тестируемости через source-only mode, основной flow через `systemctl --user restart` + post-flight через `is-active` + `pgrep`, диагностика через `journalctl` + tail XDG-лога.
- **`watch_and_restart.sh`** — переписан целиком — `inotifywait` из пакета `inotify-tools` вместо polling каждые 2 секунды, debounce 1 секунда, `systemctl --user restart` вместо самописного kill + start, проверка наличия `inotifywait` на старте с подсказкой по установке.
- **`start-claude-manager.sh`** — удалён — на Linux systemd сам делает retry через `Restart=always` + `RestartSec=10`, обёртка не нужна.
- **`tests/test_restart_claude_manager_script.py`** — переписан целиком — 6 тестов под новые хелперы.
- **`tests/test_start_claude_manager_script.py`** — удалён — тестировал конкретные строки удалённой macOS-обёртки.

**Phase 5 — документация и bonus-чистка (коммит `70067ac`):**

- **`CLAUDE.md`** — изменён — удалены два принципа изоляции от TCC, удалён раздел про retry-обёртку launchd, переписаны принципы verify-before-and-after и запрета самоперезапуска под systemctl, обновлены «Команды разработки», «Структура проекта», ОС-линия, путь к Claude binary; удалён буллет про `EDEADLK`.
- **`dev/docs/bot-launch-infrastructure.md`** — переписан целиком — карта компонентов запуска под Linux: systemd user service, цепочка ExecStart → entry point → Python, /restart через отвязанный subprocess, диагностика через journalctl.
- **`dev/docs/deployment-guide.md`** — переписан целиком — пошаговая инструкция установки под Ubuntu/Debian/Fedora: установка inotify-tools, настройка systemd unit, команды `systemctl --user enable/start`.
- **`dev/docs/brd/brd-user-journeys.md`** — изменён — переписан абзац про защиту от двойного запуска (CJM-09) под systemctl, заменены устаревшие пути `~/Library/Logs/` на XDG-путь, описание «бот только на маке» заменено на Linux + fcntl + systemd, поправлены примеры путей в иллюстрации алгоритма `_encode_project_path`.
- **`dev/docs/docs-index.md`** — изменён — обновлены описания `deployment-guide.md` и `bot-launch-infrastructure.md` под Linux/systemd.
- **`src/claude_manager/main.py`** — изменён — 2 комментария про LaunchAgent заменены на systemd/journalctl (только комментарии, продакшен-логика не тронута).
- **`.env.example`** — изменён — `PROJECTS_ROOT_DIR` перенесён в раздел «Обязательные настройки», убран macOS-default `/Users/ivan/Desktop/claude-sandbox`, примеры путей переведены на `/home/you/...`.
- **`tests/integration/test_claude_cli_contract.py`** — изменён — macOS fallback `/Users/ivan/.npm-global/bin/claude` заменён на универсальный `os.path.expanduser("~/.npm-global/bin/claude")`. На Linux 2 ранее skip-нутых контрактных теста теперь реально запускаются и зелёные.

**Phase 6 — финальная верификация и документирование:**

- **`dev/docs/specs/28.05_18.00-linux-bot-commands-cleanup-plan.md`** — изменён — отметки прогресса по шагам, дополнительные находки (тесты удалённой обёртки, ссылки в main.py, fallback в integration-тесте), диагностика готовности Linux-окружения, фиксация baseline-падения `test_registry_survives_reload` и предупреждение про невозможность самоперезапуска из активной сессии.
- **`dev/docs/adr/28.05_19.45-session-change-documenter-linux-only-platform-migration.md`** — создан — основной ADR этой миграции, описывает контекст, решение, все 13 связанных файлов, заменяет два предыдущих ADR/CLAUDE.md-update про postflight Python-процесса и про TCC venv-миграцию.
- **`dev/docs/claude-md-updates/28.05_19.45-session-change-documenter.md`** — создан — лог всех изменений в CLAUDE.md в формате «было / стало / причина».

## Решения

- **Решение:** удалить `start-claude-manager.sh` и связанный тест целиком, не заменять Linux-аналогом. **Причина:** на Linux systemd сам реализует retry через `Restart=always` + `RestartSec=10` — самописная shell-обёртка с retry-логикой становится избыточной. Сохранение обёртки только ради единообразия с macOS-эпохой добавило бы дублирующий механизм поверх systemd. Тест `test_start_claude_manager_script.py` проверял конкретные строки удалённой обёртки (`trap terminate_running_bot_process_tree TERM INT`, `pgrep -P "$parent_process_id"`) — без обёртки тестировать нечего, удаление логичное.

- **Решение:** `PROJECTS_ROOT_DIR` стала обязательной переменной без любого default-значения. **Причина:** старый дефолт `/Users/ivan/Desktop/claude-sandbox` был mine для нового пользователя на Linux — он не задал переменную, получил бы ошибку «папка не существует» с непонятным путём в чужой домашней директории macOS-вида. Явная `ConfigError` с упоминанием имени переменной — лучший fail-fast.

- **Решение:** оставить и переписать «Принцип запрета самоперезапуска из собственного дерева процессов», не удалять. **Причина:** принцип не привязан к macOS — он работает одинаково для launchd и для systemd. Subprocess в cgroup сервиса будет убит вместе с сервисом при `systemctl restart`, exit 137, бесконечный retry. Это эмпирически подтверждено в момент выполнения этой работы: я (Claude) запущен как subprocess под `claude-manager.service`, и попытка вызвать `./restart-claude-manager.sh` из текущей беседы убила бы моё дерево процессов вместе с ботом. Формулировка обобщена: «launchd-сервис» → «сервис под supervision».

- **Решение:** удалить test_start_claude_manager_script.py и упомянуть это как «находку плана» в комментарии плана, а не как «расширение скоупа». **Причина:** план Phase 4 предусматривал только `git rm start-claude-manager.sh`, но не подумал про связанный тест-файл. Это упущение было обнаружено через полный `pytest`-прогон после Phase 4 (тест начал падать с `FileNotFoundError`, потому что ссылался на удалённый скрипт). Решение по аналогии: удалить целиком, потому что нечего тестировать. Зафиксировать в плане как принцип «после удаления файла прогонять pytest — он найдёт связанные тесты».

- **Решение:** в Phase 5 правки `main.py` (только комментарии) и `test_claude_cli_contract.py` (fallback к claude binary) включены в Phase 5 commit как «bonus», а не в отдельный коммит. **Причина:** эти правки логически связаны с тем же предметом (последние macOS-упоминания), они мелкие, и разделять их в отдельный коммит создавало бы шум в git history. Один коммит «docs: переписать инфраструктурную документацию под Linux» с подробным описанием bonus-секции даёт цельную картину.

- **Решение:** не создавать новый changelog для этой миграции — отразить только через ADR + CLAUDE.md update log. **Причина:** changelog в этом проекте используется для фич («добавлена возможность X», «исправлен баг Y»), а это структурное архитектурное изменение — целая платформа эксплуатации заменена. Для такого ADR подходит лучше, потому что фиксирует принятое решение раз и навсегда.

## Контекст для следующей сессии

**Текущее состояние ветки `codex-support-spec-implementation-cycle`.** Шесть последовательных коммитов миграции:

1. `6594145 refactor(config): сделать PROJECTS_ROOT_DIR обязательным, удалить macOS-default`
2. `396052b fix(test_config): дефолтный PROJECTS_ROOT_DIR в _make_env для тестов`
3. `ab08cbc refactor(main): перенести лог в XDG-стандартный путь ~/.local/state/`
4. `fb4846e feat(bot): /restart через systemctl вместо launchctl kickstart`
5. `8ebe4cd chore(scripts): переписать restart/watch-скрипты под Linux + systemd`
6. `70067ac docs: переписать инфраструктурную документацию под Linux`

К ним добавится коммит документалиста (этот session report + ADR + CLAUDE.md update log + обновление docs-index) — следующая сессия его увидит как актуальный head ветки.

**Незавершённое: Phase 6 ручная верификация.** Тесты прогнаны (Task 17), всё остальное — за пользователем:

- Task 18 — `./restart-claude-manager.sh` из терминала (нельзя из активной сессии бота — это убьёт текущее дерево процессов).
- Task 19 — `/restart` в Telegram (бот сейчас работает на старом коде с PID 210627, новый код вступит в силу только после первого рестарта).
- Task 20 — smoke-тест базовых команд `/new`, `/sessions`, `/projects`, `/p1`, `Silence on/off`, `/all`.
- Task 21 — `./watch_and_restart.sh` (требует предустановки `inotify-tools` — пакет на этой машине не установлен, `which inotifywait` пустой).

**Известный baseline-баг.** `tests/integration/test_session_lifecycle.py::TestFilePersistence::test_registry_survives_reload` падает с момента до начала этой работы — тест ожидает запись без поля `summary`, а реальный реестр пишет `{"session_id": ..., "backend": ..., "summary": ""}`. Воспроизводится на чистом HEAD без правок (проверено через `git stash` в момент Phase 0). К миграции не относится — отдельная задача.

**Важный нюанс эксплуатации.** Бот запущен под systemd и я (Claude) работаю как subprocess в его cgroup — это значит, что любой вызов `restart-claude-manager.sh` или `systemctl --user restart` из активной сессии убьёт текущее дерево процессов вместе с ботом. Эта ситуация описана в принципе «запрета самоперезапуска» в CLAUDE.md. Для будущих сессий: рестарт делается ИЗ ВНЕШНЕГО терминала или через `/restart` в Telegram.

**Замены ADR/CLAUDE.md-update.** Новый ADR `28.05_19.45-...-linux-only-platform-migration.md` заменяет:

- `14.05_16.12-session-change-documenter-restart-postflight-python-process.md` — старая логика post-flight для launchd-обёртки, неактуальна на Linux.
- `claude-md-updates/03.05_10.58-venv-launchd-tcc-migration.md` — миграция venv в `~/.venvs/` ради обхода TCC, на Linux обратно перенесён в проект.

Поле `## Заменяет` в новом ADR ссылается на оба документа — оригиналы оставлены как историческая запись.

## Коммиты

- `6594145 refactor(config): сделать PROJECTS_ROOT_DIR обязательным, удалить macOS-default` — Phase 1, основная правка `config.py`.
- `396052b fix(test_config): дефолтный PROJECTS_ROOT_DIR в _make_env для тестов` — Phase 1, доп-фикс остальных тестов в `test_config.py`.
- `ab08cbc refactor(main): перенести лог в XDG-стандартный путь ~/.local/state/` — Phase 2.
- `fb4846e feat(bot): /restart через systemctl вместо launchctl kickstart` — Phase 3, плюс удаление мёртвого `import os` и тесты `TestHandleRestart`.
- `8ebe4cd chore(scripts): переписать restart/watch-скрипты под Linux + systemd` — Phase 4, плюс удаление obsolete `test_start_claude_manager_script.py`.
- `70067ac docs: переписать инфраструктурную документацию под Linux` — Phase 5, плюс bonus-правки `main.py`/`brd`/`.env.example`/`test_claude_cli_contract.py`.
- *(будущий коммит документалиста)* — ADR, CLAUDE.md update log, session report, обновление docs-index, прогресс-отметки в плане.

## Выполненные команды

- `git rm start-claude-manager.sh tests/test_start_claude_manager_script.py` — удаление macOS-обёртки и связанного теста.
- `chmod +x restart-claude-manager.sh watch_and_restart.sh` — права на исполнение для переписанных скриптов.
- `python -m pytest tests/test_restart_claude_manager_script.py -v` — прогон 6 новых тестов для shell-скрипта (все зелёные).
- `python -m pytest tests/ --ignore=tests/e2e` — полный прогон, прогонялся 4 раза в процессе работы. Финал: `1012 passed, 4 skipped, 1 failed in 102.41s`.
- `systemctl --user status claude-manager.service` — диагностика готовности Linux-окружения. Подтвердило: сервис активен 5 дней, я как Claude работаю в его cgroup, новый код не загружен (требуется рестарт).
- `ls -la ~/.npm-global/bin/claude` + `which claude` — проверка Linux-пути к Claude CLI для исправления fallback в integration-тесте.

## Проблемы и решения

- **Проблема:** после удаления `start-claude-manager.sh` упал тест `tests/test_start_claude_manager_script.py` (план Phase 4 не предусмотрел этот тест-файл). **Решение:** удалить тест целиком — он проверял конкретные строки удалённой macOS-обёртки, на Linux тестировать нечего. Зафиксировано в плане как «находка через полный pytest».

- **Проблема:** агент Phase 5 (Opus 4.7 через Agent tool) сделал две инициативы вне плана — убрал упоминание `~/Desktop/claude-sandbox/claude-code-sourcecode/` из CLAUDE.md и заменил `/Users/ivan/.npm-global/bin/claude` на `~/.npm-global/bin/claude`. **Решение:** проверить обе инициативы — они корректны (на Linux первого пути нет вообще, второй универсальный). Принять. Зафиксировать в комментариях плана.

- **Проблема:** после удаления буллета про `EDEADLK` в CLAUDE.md исчезла пустая строка между списком и заголовком `## Стандарты разработки скиллов`, что нарушило markdown-форматирование. **Решение:** точечный Edit — добавить пустую строку обратно.

- **Проблема:** ещё 4 устаревших macOS-упоминания в `brd-user-journeys.md` (вне явного плана Phase 5): «бот работает только на маке», два пути `~/Library/Logs/`, «Переименование на macOS — мгновенная операция». **Решение:** поправить в том же коммите Phase 5 как bonus — это обновление формулировок, не изменение пользовательских сценариев, BRD остаётся консистентным.

- **Проблема:** `.env.example` оставлял `PROJECTS_ROOT_DIR` в разделе «Необязательные настройки» и хранил macOS-default в комментарии, противореча Phase 1 (переменная стала обязательной). **Решение:** переместить переменную в раздел «Обязательные настройки», убрать default-комментарий, заменить примеры путей на `/home/you/...`.

- **Проблема:** `tests/integration/test_claude_cli_contract.py` использовал macOS fallback `/Users/ivan/.npm-global/bin/claude` — на Linux путь не существовал, `CLAUDE_BINARY = None`, 2 контрактных теста молча скиплись через `@pytest.mark.skipif`. Покрытие казалось «зелёным», а контракт с Claude CLI на самом деле не проверялся. **Решение:** заменить на универсальный `os.path.expanduser("~/.npm-global/bin/claude")`. После замены 2 теста реально запустились и оба зелёные — контракт подтверждён эмпирически.

- **Проблема:** Phase 6 (ручная верификация) включает `./restart-claude-manager.sh`, но я работаю как subprocess под `claude-manager.service` — вызов скрипта убьёт собственное дерево процессов вместе с ботом. **Решение:** не запускать рестарт-скрипт из активной сессии. Зафиксировать ограничение в плане и передать Tasks 18-21 пользователю.

## Результаты тестирования

- **Полный pytest-прогон (финал):** `1012 passed, 4 skipped, 1 failed in 102.41s`. По сравнению с baseline (`1009 passed, 6 skipped, 1 failed`): `+3 passed, -2 skipped`. Прирост за счёт двух новых тестов `TestHandleRestart` в Phase 3, шести новых тестов `test_restart_claude_manager_script.py` в Phase 4, минус один удалённый `test_start_claude_manager_script.py`, минус два теста стали реально запускаться вместо skip благодаря правке fallback в `test_claude_cli_contract.py`.
- **Единственное падение** — `tests/integration/test_session_lifecycle.py::TestFilePersistence::test_registry_survives_reload` — унаследованный baseline-баг, не относится к миграции.
- **Shell-тесты** (6 новых) — все зелёные за 0.07s. Тестируют `check_editable_install`, `service_is_running`, `print_diagnostics_on_failure` через source-only mode (`CLAUDE_MANAGER_RESTART_SOURCE_ONLY=1 source restart-claude-manager.sh`) с переопределением `systemctl()` / `pgrep()` / `journalctl()` как bash-функций — техника «mock через function override».
- **Диагностика Linux-окружения:** unit systemd-файл существует и enabled, сервис `active (running)` 5 дней, editable install проходит import, XDG-лог уже создан (498 KB). **Не установлен `inotify-tools`** — `watch_and_restart.sh` сам подскажет команду установки при первом запуске.

Путь к этому отчёту: `/home/ivan/claude-sandbox/claude_manager/dev/docs/session-reports/28-05/19-50_macos-to-linux-platform-migration.md`
