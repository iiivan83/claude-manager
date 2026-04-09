# Сессия 09-04: завершение миграции development/ → dev/ в скиллах

## Резюме

Закрыты незавершённые Task #8 и #15 из предыдущего отчёта `19-15_apply-dev-structure-standard.md` — обновлены ссылки `development/` → `dev/...` в 25 файлах (16 скиллов + 3 спеки + 6 архивов), всего ~215 замен. Все 386 тестов зелёные, миграция полностью закрыта.

## Изменённые файлы

| Файл | Действие | Что сделано |
|------|----------|-------------|
| `.claude/skills/session-report/SKILL.md` | изменён | 4 вхождения `development/docs/session-reports/` → `dev/docs/session-reports/`, `development/docs/docs-index.md` → `dev/docs/docs-index.md`. Починен frontmatter: `user_invocable: true` → `user-invocable: true` (баг, из-за которого скилл не регистрировался как вызываемый через `/session-report`). |
| `.claude/skills/make-script-spec/SKILL.md` | изменён | Обновлены пути к `dev/docs/docs-index.md`, `~/.claude/references/document-naming-and-placement.md` (вместо несуществующего `target-folder-structure.md`), `dev/docs/specs/`, `dev/temp/`. Работал агент A волны 1 — успел дочистить до краша на 529 overload. |
| `.claude/skills/update-project-docs/SKILL.md` | изменён | 7 вхождений: `dev/docs/docs-index.md`, `dev/docs/brd/brd-user-journeys.md`, `dev/docs/deployment-guide.md`, путь в JSON pipeline-state. |
| `.claude/skills/brd-decompose/SKILL.md` | изменён | 3 вхождения: путь к BRD (добавлена подпапка `brd/`), два пути к `dev/docs/specs/module-dependency-graph.md`. |
| `.claude/skills/run-user-testing/SKILL.md` | изменён | 22 вхождения: `dev/docs/logs/testing/` (перенесено из `development/docs/testing/`), `dev/docs/brd/brd-user-journeys.md`, `dev/docs/specs/pipeline-spec.md`, `dev/docs/specs/realised/` (с английской → британской орфографией), `dev/scripts/`. |
| `.claude/skills/update-session-report/SKILL.md` | изменён | 1 вхождение: `dev/docs/session-reports/`. |
| `.claude/skills/create-test-runner/SKILL.md` | изменён | 1 вхождение: `dev/scripts/` в проверке текущего состояния. |
| `.claude/skills/create-user-test-scenarios/SKILL.md` | изменён | 5 вхождений: BRD (2x), тест-результаты `dev/docs/logs/testing/` (3x). |
| `.claude/skills/create-phase-gate-checker/SKILL.md` | изменён | 23 вхождения (больше всех среди скиллов): `dev/scripts/` (9x), пути к спекам, BRD, review-отчётам, тест-планам, docs-index. |
| `.claude/skills/validate-brd/SKILL.md` | изменён | 8 вхождений: BRD, отчёт валидации, спеки, scripts, docs-index, pipeline-spec. |
| `.claude/skills/test-e2e/SKILL.md` | изменён | 14 вхождений: BRD, тест-планы/результаты `dev/docs/logs/testing/`, спеки, `dev/docs/specs/realised/`, `dev/scripts/run-all-tests.sh`. |
| `.claude/skills/test-integration/SKILL.md` | изменён | 9 вхождений: граф зависимостей, BRD, realised-спеки, scripts, pipeline-spec. |
| `.claude/skills/rename-entity/SKILL.md` | изменён | 3 вхождения: область поиска ссылок `dev/` вместо `development/` (раздел «Область действия», шаг 3, «Важные детали»). |
| `.claude/skills/pipeline-run/SKILL.md` | изменён | 29 вхождений: BRD, brd-validation-report, `dev/docs/specs/`, `dev/docs/specs/realised/`, тест-пути, scripts, deployment-guide, docs-index, scripts-registry, review-report. |
| `.claude/skills/dev-script-implement/SKILL.md` | изменён | 7 вхождений: `dev/docs/specs/` (переезд из script-specs/), `dev/docs/specs/realised/`. |
| `.claude/skills/implement-module/SKILL.md` | изменён | 3 вхождения в теле файла (description был уже обновлён в предыдущей сессии). |
| `dev/docs/specs/module-dependency-graph.md` | изменён | 2 вхождения — ссылки на BRD и отчёт валидации в заголовке. |
| `dev/docs/specs/pipeline-spec.md` | изменён | **60 вхождений** (самый большой файл): `dev/docs/logs/testing/`, `dev/docs/specs/`, `dev/docs/specs/realised/`, `dev/scripts/`, BRD, brd-validation-report, review-report, deployment-guide, review-checklists. |
| `dev/docs/specs/autofix-e2e-skill-prompt.md` | изменён | 3 вхождения: root-cause-reports, session-reports, `dev/temp/autofix-state.json`. |
| `dev/temp/prompt-for-spec-pipeline-skill.md` | изменён | 2 вхождения в списках вариантов. |
| `dev/docs/brd/brd-validation-report_29-03-23-47.md` | изменён | 2 вхождения исправлено (строки 4, 65), **4 оставлены** как историческая запись состояния на 29.03 (строка 53 — описание удалённого файла-сироты, 59 — констатация пустых папок, 70/72 — «Проблема 2.4: папка development/docs/testing/ не существует»). |
| `dev/docs/logs/testing/e2e-test-plan_30-03-02-20.md` | изменён | 1 вхождение — ссылка на BRD в заголовке. |
| `dev/docs/logs/testing/e2e-test-plan_30-03-11-38.md` | изменён | 1 вхождение — ссылка на BRD. |
| `dev/docs/logs/testing/e2e-test-plan_30-03-12-29.md` | изменён | 1 вхождение — ссылка на BRD. |
| `dev/docs/logs/testing/e2e-test-plan_30-03-12-58.md` | изменён | 1 вхождение — ссылка на BRD. |
| `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude-manager/memory/feedback_use_agents_for_bulk_work.md` | создан | Новое правило памяти: для массовых правок (>3-4 файлов) делегировать агентам, параллельно, узкими группами, чтобы не нарываться на 529 overload. |
| `~/.claude/projects/-Users-ivan-Desktop-claude-sandbox-claude-manager/memory/MEMORY.md` | изменён | Добавлена ссылка на новый файл feedback_use_agents_for_bulk_work.md в индексе. |

## Выполненные команды

- `grep -rln "development/" --include="*.md" --include="*.py" --include="*.sh" --include="*.json" --include="*.toml" --include="*.txt" --exclude-dir=...` — начальный поиск, нашёл 25 файлов с устаревшим путём. Grep tool использовать нельзя из-за взаимодействия с `.gitignore`, в котором `.claude/` игнорируется с исключением `!.claude/skills/`.
- `grep -rln "development/" ...` — промежуточный поиск после волны 1, показал что осталось 10 файлов (подтвердил, что агент A до краха успел сделать make-script-spec).
- `grep -rn "development/" ...` — финальный поиск, EXIT=1 (ничего не найдено), подтвердил чистоту.
- `source .venv/bin/activate && python -m pytest tests/ -x --tb=short` — полный прогон тестов после миграции. **386 passed за 10 секунд, 3 warnings** (все warnings — от deprecated `retry_after` в telegram-боте, не связаны с миграцией).
- `ps aux | grep -E "claude_manager|watch_and_restart"` — проверка процессов бота. Бот работает (PID 2150), запущен с вторника через LaunchAgent.
- `launchctl list | grep claude-manager` — отклонено пользователем. Перезапуск бота не понадобился.

## Решения

- **Использовать агентов для всей массовой правки (вторая волна).** Причина: пользователь явно попросил «делай все через агентов пожалуйста». Это устойчивый паттерн — он уже просил так же в предыдущей сессии `19-15_apply-dev-structure-standard.md`. Записано в память как `feedback_use_agents_for_bulk_work.md`.

- **Дробить агентов на узкие группы по 3-4 файла.** Причина: в волне 1 из 5 агентов три получили `API Error: 529 Overloaded` (один — после 15 tool_use и 123 сек, два — сразу на 0 tool_use). Волна 2 с 3 агентами по 3-4 файла прошла без сбоев — это эмпирически подтверждает, что параллелизм надо дозировать и делить работу на узкие куски.

- **Карта замен от специфичных к общим.** Причина: простая замена `development/` → `dev/` сломала бы пути, потому что внутренняя структура изменилась. Например, `development/docs/brd-user-journeys.md` → `dev/docs/brd/brd-user-journeys.md` (добавлена подпапка `brd/`), `development/docs/testing/` → `dev/docs/logs/testing/` (testing переехал в logs/), `development/specs/realized/` → `dev/docs/specs/realised/` (добавлены `docs/` + смена орфографии с американской на британскую). Поэтому карта замен шла от длинных специфичных путей к коротким общим.

- **Ссылки на несуществующий `target-folder-structure.md` заменять на `~/.claude/references/document-naming-and-placement.md`.** Причина: `target-folder-structure.md` был удалён при миграции, а `document-naming-and-placement.md` — глобальный референс с теми же правилами размещения документов. В итоге ни в одном из 25 файлов ссылка на `target-folder-structure.md` не встретилась — заменять не пришлось, но правило было подготовлено заранее.

- **Не трогать 4 исторических упоминания в `brd-validation-report_29-03-23-47.md`.** Причина: этот отчёт — зафиксированная фотография состояния проекта на 29.03.2026. Строки вроде «Проблема 2.4: Папка development/docs/testing/ не существует» — это констатация факта на момент валидации, и её переписывание исказило бы историческую правду. Правка путей уместна только в актуальных документах, которые используются как рабочие инструкции.

- **НЕ перезапускать telegram-бота после миграции.** Причина: изменения были только в файлах скиллов `.claude/skills/*.md`, а код бота `src/claude_manager/*.py` не менялся. Архитектура Claude Manager такая, что бот запускает Claude Code CLI как новый subprocess на каждое сообщение пользователя (см. `src/claude_manager/claude_runner.py`), и subprocess читает актуальные файлы скиллов с диска при каждом запуске. Значит изменения в скиллах автоматически подхватываются без перезапуска бота. Правило `feedback_restart_bot.md` применяется только к изменениям в `.py` файлах бота.

- **Починить баг `user_invocable` → `user-invocable` попутно.** Причина: диагностика Claude Code в момент правки session-report показала предупреждение об unsupported attribute. Это баг в эталонном скилле — из-за которого `/session-report` формально не регистрировался как user-invocable. Правильнее починить сразу, раз файл уже редактируется по делу, чем оставлять до следующей сессии.

## Проблемы и решения

- **Проблема**: 3 из 5 агентов волны 1 получили `API Error: 529 Overloaded`. Агент A упал после 15 tool_use и 123 секунд, агенты C и D — сразу на 0 tool_use. **Решение**: промежуточная верификация grep показала, что агент A успел сделать make-script-spec до краха, а агенты B и E прошли успешно. Запущена волна 2 с меньшим параллелизмом (3 агента вместо 5) и меньшими группами (3-4 файла вместо 4), все прошли без сбоев.

- **Проблема**: Grep tool возвращал «No files found» в `.claude/skills/` несмотря на реальное наличие совпадений. **Причина**: взаимодействие `.gitignore` (`.claude/` + `!.claude/skills/`) с ripgrep под капотом Grep tool. **Решение**: все поиски делал через bash `grep -r` с явными `--exclude-dir`. Это правило уже было в отчёте предыдущей сессии, так что сразу применил.

- **Проблема**: отказ пользователя на `launchctl list`. **Решение**: не нужно было — перезапуск бота вообще не требовался (изменения только в скиллах, не в коде бота). Проверил работу бота через `ps aux` — PID 2150 активен.

- **Проблема**: диагностика обнаружила `user_invocable` с подчёркиванием в session-report/SKILL.md, когда поддерживается `user-invocable` с дефисом. **Решение**: починил одновременно с заменой путей, не откладывая.

## Незавершённое

- [ ] **Коммит изменений.** Пользователь попросил сделать коммит сразу после этого отчёта — будет сделан следующим шагом через скилл `commit`.

- [ ] **Удаление бэкапа `.claude/skills/.backup-2026-04-09/`.** Бэкап страховал миграцию (11 элементов: 10 старых скиллов + `AGENTS.md.old`). Сейчас все 33 скилла работают, 386 тестов зелёные — бэкап можно удалить. Но решение оставляю пользователю, потому что удаление необратимо, а место он не занимает критично.

## Контекст для следующей сессии

### Общее состояние проекта

- Миграция `development/` → `dev/` **полностью завершена**. Финальный `grep -rn "development/"` возвращает EXIT=1 (чисто), кроме 4 исторических упоминаний в `brd-validation-report_29-03-23-47.md` (умышленно сохранены).
- **386 тестов зелёные** (`python -m pytest tests/`). Миграция не сломала ни одного теста.
- Telegram-бот работает, PID 2150, запущен через LaunchAgent с вторника. Не перезапускался — не требовалось.
- Бэкап старых скиллов `.claude/skills/.backup-2026-04-09/` всё ещё на месте.
- Индексы `dev/docs/docs-index.md` и `dev/docs/skill-index.md` — актуальны (не трогались в этой сессии).

### Новое в памяти

Добавлено правило `feedback_use_agents_for_bulk_work.md`: для массовых правок (>3-4 файлов) делегировать работу агентам через Agent tool, параллельно, узкими группами по 3-5 файлов (до 20 tool_use на агента), чтобы не нарываться на API 529 Overloaded. Это ответ на два последовательных запроса пользователя в 09-04: сначала в `19-15_apply-dev-structure-standard.md`, потом в этой сессии.

### Что стоит знать для следующей сессии

- **Скилл `session-report` починен** и теперь сохраняет отчёты в правильное место `dev/docs/session-reports/DD-MM/`. До этой сессии он сохранял бы в несуществующую `development/docs/session-reports/`. Теперь его можно вызывать через `/session-report` без подсказок в args.
- **Все 16 скиллов, которые раньше ссылались на `development/`, теперь актуальны** и подтянут правильные пути при запуске.
- **Правило `feedback_use_agents_for_bulk_work`** — если будет следующая массовая правка, сразу делегируй агентам, не редактируй в основном контексте.
- **`target-folder-structure.md` НЕ существует** — если где-то увидишь ссылку, заменяй на `~/.claude/references/document-naming-and-placement.md`.
- **Grep tool ложно возвращает «No files found»** в `.claude/skills/` из-за `.gitignore` — всегда используй bash `grep -r` с `--exclude-dir`.
- **Не перезапускай бота после изменений в скиллах** — только после изменений в `src/claude_manager/*.py`. Claude Code CLI читает скиллы при каждом subprocess.

### Нерешённые технические вопросы (если продолжать работу с этой областью)

- Бэкап `.claude/skills/.backup-2026-04-09/` — оставить или удалить? Решение за пользователем. После положительного прогона тестов и недели работы без сюрпризов — безопасно удалить.
- Новый скилл `update-skill` (эталонная версия 456 строк) поддерживает сравнительное тестирование old vs new. Его можно использовать для синхронизации эталонных скиллов с глобальными `~/.claude/skill-templates/`, если они будут обновляться.
