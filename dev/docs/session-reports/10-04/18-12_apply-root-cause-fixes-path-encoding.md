# Сессия 10-04: apply-root-cause-fixes для session-reader-path-encoding

## Резюме

Пайплайн `apply-root-cause-fixes` применил все 10 рекомендаций из root-cause отчёта `10-04_16-02_session-reader-path-encoding.md`: корневой баг в `_encode_project_path` починен, бот снова видит папку сессий Claude CLI, warning «Папка сессий не найдена» исчез, живой мониторинг через `session_watcher` заработал. Верификатор одобрил все 10 пунктов (7 без правок, 3 с замечаниями), 0 отклонено, 0 ошибок применения.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `src/claude_manager/session_reader.py` | изменён | R4: функция `_encode_project_path` переписана через `SANITIZE_PATH_PATTERN = re.compile(r"[^a-zA-Z0-9]")`. Константа добавлена рядом с `XML_TAG_PATTERN`/`WHITESPACE_PATTERN` (строки 45-48), тело функции — `SANITIZE_PATH_PATTERN.sub("-", project_dir)` (строки 63-69). Docstring расширен со ссылкой на `sanitizePath()` из Claude CLI |
| `tests/test_session_reader.py` | изменён | R5+R6: добавлено 6 регрессионных тестов — `test_path_with_underscores` (наш упавший случай), `test_path_with_dots`, `test_path_with_mixed_special_chars`, `test_path_with_digits`, `test_path_with_cyrillic` (зафиксирован реальный вывод `-Users-ivan-------` через прогон regex в Python), плюс `test_builds_path_with_underscore` в `TestBuildSessionsPath` |
| `tests/integration/test_claude_cli_contract.py` | создан | R7: новый интеграционный тест с реальным Claude CLI. Запускает `claude -p --output-format text --dangerously-skip-permissions --max-budget-usd 1 --tools "" --disable-slash-commands` в `tmp_path.resolve()` с подчёркиванием в имени, сверяет созданную папку в `~/.claude/projects/` с выводом `_encode_project_path`. Скипается через `shutil.which("claude") is None`, таймаут 60s, очистка в `finally`. Учтён macOS-нюанс: pytest `tmp_path` — симлинк, Claude CLI резолвит через `realpath`, поэтому `project_dir.resolve()` обязателен |
| `CLAUDE.md` | изменён | R1: на строке 195 добавлен пункт «Контракты с внешними системами проверяются эмпирически, а не по догадке» после пункта про `stream-json`, в разделе «Важные детали для разработки». Указан путь к исходникам Claude CLI: `~/Desktop/claude-sandbox/claude-code-sourcecode/` |
| `dev/docs/specs/realised/session_reader_spec.md` | изменён | R2: три правки — описание `_encode_project_path` со ссылкой на `sessionStoragePortable.ts:311`, раздел «Алгоритм» (один пункт через regex вместо двух), новый подраздел «Внешние контракты» в «Зависимостях» |
| `dev/docs/session-reports/10-04/15-47_switch-bot-working-dir.md` | создан | R3: добавлен блок «Поправка 10-04 16:02» в конец файла с указанием на ошибочную диагностику warning «Папка сессий не найдена». Оригинал не тронут. Файл untracked в git до этого пайплайна |
| `.claude/skills/spec-module/SKILL.md` | изменён | R9: в шаблон спецификации добавлен раздел «Контракты с внешними системами» (между «Обработка ошибок» и «Константы») с тремя обязательными пунктами (источник правды, точный алгоритм, тест-план). В «Шаг 1: Сбор контекста» добавлен подпункт 1.6 про исходники внешних инструментов |
| `.claude/skills/review-code/SKILL.md` | изменён | R10: в Проход 3 (Архитектура) добавлен пятый чек-пункт «Контракты с внешними системами» со ссылкой на принцип из `CLAUDE.md` |
| `dev/docs/logs/root-cause-fixes/10.04_17.44-apply-root-cause-fixes-session-reader-path-encoding/orchestrator-log.json` | создан | Лог пайплайна: все 5 фаз со статусами, деталями по каждой рекомендации, списком применённых файлов, runtime-верификацией |
| `dev/docs/logs/root-cause-reports/realized/10-04_16-02_session-reader-path-encoding.md` | перемещён | Архивация root-cause отчёта в `realized/` после успешного применения всех рекомендаций (условие ноль ошибок выполнено) |
| `~/Library/Logs/claude-manager.error.log` | очищен | R8: после применения R4 + перезапуск бота + проверка что warning перестали — truncate через `: > файл`. Размер до: 301 МБ, после: 0 B. Inode сохранён, file descriptor бота остался рабочим |

## Коммиты

- `afaaf45` — fix(session_reader): воспроизведение реального алгоритма sanitizePath из Claude CLI. 10 файлов, +721, −6. Содержит все 10 применённых рекомендаций, архив root-cause отчёта, лог пайплайна

## Выполненные команды

- `launchctl unload ~/Library/LaunchAgents/com.ivan.claude-manager.plist && launchctl load ...` — перезапуск бота после применения R4 (старый PID 82370 → новый 32358)
- `.venv/bin/python -m pytest tests/test_session_reader.py -v` — 37 passed за 0.05s, включая 6 новых регрессионных тестов
- `.venv/bin/python -m pytest tests/integration/test_claude_cli_contract.py -v` — 1 passed за 5.86s с реальным Claude CLI
- `: > ~/Library/Logs/claude-manager.error.log` — очистка лога в месте (R8)
- `mv dev/docs/logs/root-cause-reports/10-04_16-02_...md dev/docs/logs/root-cause-reports/realized/` — архивация отчёта
- `python3 -c "import re; print(repr(re.sub(r'[^a-zA-Z0-9]', '-', '/Users/ivan/Проект')))"` — эмпирическая фиксация ASCII-поведения regex для кейса `test_path_with_cyrillic`
- `git add <10 файлов по именам> && git commit` — точечное добавление только файлов пайплайна, не `git add -A`

## Решения

- **Верификатор запускается даже при готовой секции «Верификация решений» в root-cause отчёте.** Причина: защита от confirmation bias — автор отчёта внутри контекста расследования склонен одобрять свои же предложения, независимый агент приходит «со стороны» с чек-листом из 5 критериев.
- **R5 и R6 объединены в одного агента.** Причина: оба меняют один файл `tests/test_session_reader.py`, параллельный запуск создал бы классическую lost-update гонку (первый читает → второй читает → первый пишет → второй затирает).
- **R1 применяется перед R9/R10.** Причина: оба скилла (`spec-module` и `review-code`) ссылаются на принцип эмпирической верификации из `CLAUDE.md`, без R1 ссылки указывали бы на несуществующий пункт.
- **R8 (очистка лога) выполняется строго после R4 + перезапуск + проверка.** Причина: замечание верификатора — если почистить лог до фикса, watcher через 2 секунды снова зальёт файл warning-ами, и вся работа по очистке пойдёт в мусор.
- **Коммит через точечный `git add` по именам файлов, не `git add -A`.** Причина: в `git status` было 13 pre-existing модифицированных файлов и около 20 untracked, большинство к задаче не относится. `git add -A` смешал бы мои изменения с чужими, сделав ревью невозможным.
- **Для R7 обязателен `tmp_path.resolve()` на macOS.** Причина: pytest `tmp_path` на macOS возвращает симлинк `/var/folders/...`, а Claude CLI резолвит `cwd` через `realpath` перед кодированием имени папки. Без `resolve()` ожидаемое и реальное имена не совпадают, тест падает на пустом месте.

## Проблемы и решения

- **Проблема:** `pytest` сначала взял python из `/Users/ivan/Desktop/claude-sandbox/su-main-master2/django/.venv/bin/python3` (чужой venv), упал с `No module named pytest`. **Решение:** явный вызов через `.venv/bin/python -m pytest` из корня проекта.
- **Проблема:** `git add dev/docs/logs/root-cause-reports/10-04_16-02_...md` упал с `pathspec did not match any files` — старый путь был untracked, git про него не знал. **Решение:** добавлять только новый путь в `realized/`, старый автоматически исчез после `mv`.
- **Проблема:** первая попытка запустить `python3 -m pytest` через системный Python привела к ошибке. **Решение:** всегда использовать `.venv/bin/python` для этого проекта (это стандарт, записанный в `CLAUDE.md` → команды разработки → `source .venv/bin/activate`).

## Незавершённое

- [ ] **Новый warning «Нет timestamp в файле сессии»** — после починки R4 обнажился вторичный warning для 8 файлов `.jsonl` в правильной папке `-Users-ivan-Desktop-claude-sandbox-claude-manager`. Это класс проблем из раздела «Дополнительные наблюдения» root-cause отчёта (там было про 4 файла в `budgets`, теперь — про 8 в `claude-manager`). Функция `_read_session_file` логирует warning на каждой итерации watcher-а при отсутствии `timestamp` в первой строке. Нужен отдельный root-cause или тикет: либо пропускать такие файлы молча (warning один раз на старте), либо удалить их как битые. Частота новых warning меньше старых примерно в 18 раз (8 файлов против 142), но шум в логе остаётся.
- [ ] **Ротация error.log** — R8 включал опциональную часть про `RotatingFileHandler` в `src/claude_manager/main.py:_setup_logging`, но в пайплайне применялась только очистка, ротация отложена как отдельный коммит. Сейчас лог снова может вырасти (хоть и медленнее), защиты от этого нет.
- [ ] **Потеря текста при гонке параллельных сообщений** — упомянуто в отчёте как «Дополнительные наблюдения». `bot.py:396-404` при `ProcessManagerError` не сохраняет текст второго сообщения пользователя в очередь. За 11 дней 14 случаев в логе. Требует отдельного root-cause или спека.
- [ ] **Проблема `_acquire_lock` в `main.py:50`** — открывает lock-файл в режиме `"w"` и трункирует PID предыдущего процесса до вызова `fcntl.flock()`. Фикс на 4 строки (открыть в `"r+"`, после успешного flock — `truncate(0)` + write). Тоже из «Дополнительных наблюдений», отложено.

## Контекст для следующей сессии

**Что починено и работает:**
- Функция `_encode_project_path` в `src/claude_manager/session_reader.py` теперь воспроизводит точный алгоритм `sanitizePath()` из Claude CLI через regex `[^a-zA-Z0-9]`. Для пути `/Users/ivan/Desktop/claude-sandbox/claude_manager` возвращает `-Users-ivan-Desktop-claude-sandbox-claude-manager` (с дефисом, совпадает с реальной папкой Claude CLI).
- Бот работает через LaunchAgent, текущий PID 32358. Живой мониторинг через `session_watcher` находит правильную папку, новые сообщения Claude приходят в Telegram через оба канала (прямой stdout + watcher-файлы).
- Error.log очищен, размер 0 B (был 301 МБ).
- Все 37 юнит-тестов `tests/test_session_reader.py` + 1 интеграционный `tests/integration/test_claude_cli_contract.py` зелёные.

**Что изменилось архитектурно:**
- В `CLAUDE.md` → «Важные детали для разработки» теперь есть принцип «Контракты с внешними системами проверяются эмпирически, а не по догадке». Агенты будущих сессий получат его в контексте автоматически.
- В `.claude/skills/spec-module/SKILL.md` шаблон спецификации требует обязательный раздел «Контракты с внешними системами» для любой функции, формирующей путь/имя/идентификатор для внешнего инструмента.
- В `.claude/skills/review-code/SKILL.md` Проход 3 (Архитектура) содержит чек-пункт, который ловит такие случаи на ревью.
- Root-cause отчёт заархивирован в `dev/docs/logs/root-cause-reports/realized/` — это сигнал «проблема решена, не возвращаться».
- Исходники Claude Code CLI лежат в `~/Desktop/claude-sandbox/claude-code-sourcecode/` — это единственный источник правды для алгоритмов Claude CLI, его нужно читать перед фиксацией контрактов в спеках.

**Важные наблюдения про macOS:**
- pytest `tmp_path` возвращает симлинк `/var/folders/...` → `/private/var/folders/...`. Любой subprocess, который резолвит `cwd` через `realpath` (Claude CLI делает именно так), увидит развёрнутый путь. Интеграционные тесты должны использовать `tmp_path.resolve()` перед сравнением имён.
- Бот запущен через LaunchAgent (`com.ivan.claude-manager`), стандартный способ перезапуска — `launchctl unload && launchctl load ~/Library/LaunchAgents/com.ivan.claude-manager.plist`.

**Что ждать в error.log сейчас:**
- Warning «Папка сессий не найдена» — **не должно быть совсем**. Если появятся снова — регрессия R4.
- Warning «Нет timestamp в файле сессии» — будет идти для 8 файлов раз в 2 секунды до тех пор, пока не починят отдельным тикетом.
- Остальное — штатные операции, ошибки от Telegram/сети редкие.

**Папка логов пайплайна:** `dev/docs/logs/root-cause-fixes/10.04_17.44-apply-root-cause-fixes-session-reader-path-encoding/` — внутри `orchestrator-log.json` с полной хронологией всех 5 фаз, деталями по каждой рекомендации, именами применённых файлов. Полезно для диагностики, если что-то сломается.
